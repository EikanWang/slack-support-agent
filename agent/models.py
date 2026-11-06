import logging
import re
import threading
import time
from dataclasses import dataclass

from agent.opencode_client import ModelRef, OpenCodeClient

logger = logging.getLogger(__name__)

MODEL_PATTERN = re.compile(
    r"^\s*(?:/)?model(?:s)?(?:\s+(?P<argument>\S.*?))?\s*$", re.IGNORECASE
)
RESET_WORDS = ("reset", "default", "clear")
LIST_WORDS = ("list", "ls")
CATALOGUE_TTL_SECONDS = 300.0
MAX_LISTED = 15


class ModelCommandError(Exception):
    pass


@dataclass(frozen=True)
class ModelCommand:
    argument: str | None


class ModelCatalogue:
    def __init__(self, ttl_seconds: float = CATALOGUE_TTL_SECONDS):
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._models: list[ModelRef] = []
        self._fetched_at = 0.0

    def models(self, client: OpenCodeClient) -> list[ModelRef]:
        with self._lock:
            if self._stale():
                self._models = client.available_models()
                self._fetched_at = time.time()
            return self._models

    def invalidate(self) -> None:
        with self._lock:
            self._models = []

    def _stale(self) -> bool:
        return not self._models or time.time() - self._fetched_at > self._ttl_seconds


catalogue = ModelCatalogue()


def parse_model_command(text: str) -> ModelCommand | None:
    """Return the parsed command, or None when the text is not one."""
    match = MODEL_PATTERN.match(text)
    if match is None:
        return None
    return ModelCommand(argument=match.group("argument"))


def search(models: list[ModelRef], query: str) -> list[ModelRef]:
    terms = [term for term in re.split(r"[\s/]+", query.lower()) if term]
    if not terms:
        return []
    return [
        model for model in models if all(term in str(model).lower() for term in terms)
    ]


def resolve(models: list[ModelRef], query: str) -> ModelRef:
    """Pick exactly one model, preferring an exact `provider/model` match."""
    wanted = query.strip()
    for model in models:
        if str(model).lower() == wanted.lower():
            return model

    matches = search(models, wanted)
    if not matches:
        raise ModelCommandError(
            f"No model matches `{wanted}`. Try `model list {wanted.split('/')[0]}`."
        )
    exact_ids = [m for m in matches if m.model_id.lower() == wanted.lower()]
    if len(exact_ids) == 1:
        return exact_ids[0]
    if len(matches) > 1:
        raise ModelCommandError(
            f"`{wanted}` matches {len(matches)} models. Be more specific:\n"
            + format_list(matches)
        )
    return matches[0]


def format_list(models: list[ModelRef]) -> str:
    shown = [f"• `{model}`" for model in models[:MAX_LISTED]]
    if len(models) > MAX_LISTED:
        shown.append(f"_…and {len(models) - MAX_LISTED} more._")
    return "\n".join(shown)


def handle_model_command(
    argument: str | None,
    client: OpenCodeClient,
    current: ModelRef | None,
) -> tuple[str, ModelRef | None, bool]:
    """Return the Slack reply, the thread's model, and whether it changed."""
    default = client.model
    in_use = current or default

    if argument is None:
        suffix = "" if current else " _(default)_"
        reply = (
            f"This thread is using `{in_use}`{suffix}.\n"
            "Switch with `model <name>`, browse with `model list <text>`, "
            "or go back to the default with `model reset`."
        )
        return reply, current, False

    argument = argument.strip()
    lowered = argument.lower()

    if lowered in RESET_WORDS:
        if current is None:
            return f"Already on the default model, `{default}`.", None, False
        return f"Back to the default model, `{default}`.", None, True

    words = lowered.split(maxsplit=1)
    if words[0] in LIST_WORDS:
        query = words[1] if len(words) > 1 else ""
        models = _fetch(client)
        matches = search(models, query) if query else models
        if not matches:
            return f"No model matches `{query}`.", current, False
        heading = (
            f"{len(matches)} models match `{query}`:"
            if query
            else f"{len(matches)} models available:"
        )
        return f"{heading}\n{format_list(matches)}", current, False

    chosen = resolve(_fetch(client), argument)
    if chosen == in_use:
        return f"Already using `{chosen}`.", current, False
    if chosen == default:
        return f"Back to the default model, `{default}`.", None, True
    reply = (
        f"Switched to `{chosen}`. The thread keeps its history; "
        f"`model reset` goes back to `{default}`."
    )
    return reply, chosen, True


def _fetch(client: OpenCodeClient) -> list[ModelRef]:
    try:
        return catalogue.models(client)
    except Exception as error:
        logger.warning("Could not list OpenCode models: %s", error)
        raise ModelCommandError(
            "Could not reach the OpenCode server to list models."
        ) from error
