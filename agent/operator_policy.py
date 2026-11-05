import os
from dataclasses import dataclass
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

DEFAULT_SYSTEM_DENIED = (
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/sudoers.d",
    "/etc/ssh",
    "/root",
)

GATED_PERMISSIONS = ("bash", "edit", "write", "webfetch", "task")
FILE_PERMISSIONS = ("read", "glob", "grep", "list", "edit", "write")


class OperatorPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class OperatorPolicy:
    operator_user_ids: frozenset[str]
    root: Path
    home: Path
    denied_paths: tuple[Path, ...]

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
        home_value = env.get("CASEY_OPERATOR_HOME", "").strip()
        home = Path(home_value).expanduser().resolve() if home_value else Path.home()

        denied: list[Path] = []
        if root is not None:
            denied_names = env.get("CASEY_OPERATOR_DENY", "").strip()
            names = (
                [n.strip() for n in denied_names.split(",") if n.strip()]
                if denied_names
                else [*DEFAULT_DENIED_NAMES, *DEFAULT_SYSTEM_DENIED]
            )
            for name in names:
                candidate = Path(name).expanduser()
                if not candidate.is_absolute():
                    candidate = home / candidate
                candidate = candidate.resolve()
                if cls._is_within(candidate, root):
                    denied.append(candidate)
            credentials_file = env.get("CASEY_CREDENTIALS_FILE", "").strip()
            if credentials_file:
                denied.append(Path(credentials_file).expanduser().resolve())

        return cls(
            operator_user_ids=user_ids,
            root=root if root is not None else Path("/nonexistent"),
            home=home,
            denied_paths=tuple(dict.fromkeys(denied)),
        )

    @property
    def start_directory(self) -> Path:
        if self._is_within(self.home, self.root) and self.home.is_dir():
            return self.home
        return self.root

    @property
    def enabled(self) -> bool:
        return bool(self.operator_user_ids)

    def is_operator(self, user_id: str | None) -> bool:
        return bool(user_id) and user_id in self.operator_user_ids

    def resolve_directory(self, requested: str, current: Path | None = None) -> Path:
        base = current or self.start_directory
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
        rules = [
            PermissionRule("external_directory", "*", "deny"),
            PermissionRule(
                "external_directory", os.path.join(self.root, "**"), "allow"
            ),
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
            for pattern in self._bash_patterns(denied):
                rules.append(PermissionRule("bash", pattern, "deny"))
        return rules

    def describe_denied(self) -> str:
        return ", ".join(f"`{path}`" for path in self.denied_paths)

    @staticmethod
    def _is_within(path: Path, ancestor: Path) -> bool:
        return path == ancestor or ancestor in path.parents

    def _bash_patterns(self, denied: Path) -> tuple[str, ...]:
        if self._is_within(denied, self.home) and denied != self.home:
            return (f"*{denied.name}*",)
        if denied.is_dir():
            return (f"*{denied}/*", f"*{denied}")
        return (f"*{denied}*", f"*{denied.name}*")

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
