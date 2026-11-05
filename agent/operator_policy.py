import os
from dataclasses import dataclass, field
from pathlib import Path

from agent.opencode_client import PermissionRule

DEFAULT_DENIED_NAMES = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config",
    ".slack",
    ".opencode",
    ".env",
    ".git-credentials",
    ".netrc",
)

GATED_PERMISSIONS = ("bash", "edit", "write", "webfetch", "task")
FILE_PERMISSIONS = ("read", "glob", "grep", "list", "edit", "write")


class OperatorPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class OperatorPolicy:
    operator_user_ids: frozenset[str]
    root: Path
    denied_paths: tuple[Path, ...]
    extra_denied_globs: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "OperatorPolicy":
        env = os.environ if environ is None else environ
        user_ids = frozenset(
            uid.strip()
            for uid in env.get("CASEY_OPERATOR_USER_IDS", "").split(",")
            if uid.strip()
        )
        root_value = env.get("CASEY_OPERATOR_ROOT", "").strip()
        root = Path(root_value).expanduser().resolve() if root_value else None
        if user_ids and root is None:
            raise OperatorPolicyError(
                "CASEY_OPERATOR_ROOT must be set when CASEY_OPERATOR_USER_IDS is set"
            )

        denied: list[Path] = []
        if root is not None:
            denied_names = env.get("CASEY_OPERATOR_DENY", "").strip()
            names = (
                [n.strip() for n in denied_names.split(",") if n.strip()]
                if denied_names
                else list(DEFAULT_DENIED_NAMES)
            )
            for name in names:
                candidate = Path(name).expanduser()
                if not candidate.is_absolute():
                    candidate = root / candidate
                denied.append(candidate.resolve())
            credentials_file = env.get("CASEY_CREDENTIALS_FILE", "").strip()
            if credentials_file:
                denied.append(Path(credentials_file).expanduser().resolve())

        return cls(
            operator_user_ids=user_ids,
            root=root if root is not None else Path("/nonexistent"),
            denied_paths=tuple(denied),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.operator_user_ids)

    def is_operator(self, user_id: str | None) -> bool:
        return bool(user_id) and user_id in self.operator_user_ids

    def resolve_directory(self, requested: str, current: Path | None = None) -> Path:
        base = current or self.root
        expanded = Path(requested.strip()).expanduser()
        candidate = (expanded if expanded.is_absolute() else base / expanded).resolve()

        if not self._is_within(candidate, self.root):
            raise OperatorPolicyError(
                f"`{candidate}` is outside the allowed root `{self.root}`"
            )
        for denied in self.denied_paths:
            if self._is_within(candidate, denied):
                raise OperatorPolicyError(f"`{candidate}` is a protected location")
        if not candidate.is_dir():
            raise OperatorPolicyError(f"`{candidate}` is not a directory")
        return candidate

    def permission_rules(self, directory: Path) -> list[PermissionRule]:
        root = str(self.root)
        rules = [
            PermissionRule("external_directory", "*", "deny"),
            PermissionRule("external_directory", f"{root}/**", "allow"),
        ]
        for permission in GATED_PERMISSIONS:
            rules.append(PermissionRule(permission, "*", "ask"))
        for denied in self.denied_paths:
            if denied.is_dir():
                for pattern in (f"{denied}/*", f"{denied}/**"):
                    rules.append(PermissionRule("external_directory", pattern, "deny"))
            for pattern in self._file_tool_patterns(denied, directory):
                for permission in FILE_PERMISSIONS:
                    rules.append(PermissionRule(permission, pattern, "deny"))
            rules.append(PermissionRule("bash", f"*{denied.name}*", "deny"))
        return rules

    def describe_denied(self) -> str:
        return ", ".join(f"`{path}`" for path in self.denied_paths)

    @staticmethod
    def _is_within(path: Path, ancestor: Path) -> bool:
        return path == ancestor or ancestor in path.parents

    @staticmethod
    def _file_tool_patterns(denied: Path, directory: Path) -> tuple[str, ...]:
        relative = os.path.relpath(denied, directory)
        bases = {str(denied), relative}
        patterns: set[str] = set()
        for base in bases:
            patterns.add(base)
            if denied.is_dir():
                patterns.add(f"{base}/*")
                patterns.add(f"{base}/**")
        return tuple(sorted(patterns))
