import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from agent.approvals import approval_router
from agent.deps import CaseyDeps
from agent.opencode_client import (
    ModelRef,
    OpenCodeClient,
    OpenCodeUnavailable,
    raise_for_message_error,
)
from agent.operator_policy import OperatorPolicy, OperatorPolicyError
from agent.support_agent import get_client

logger = logging.getLogger(__name__)

DEFAULT_TURN_TIMEOUT_SECONDS = 1800.0
MAX_TOOL_LINES = 12

OPERATOR_SYSTEM_PROMPT = """\
You are Casey in operator mode: a coding and shell assistant controlling the \
owner's workstation through Slack. The person talking to you is the machine's \
owner and has authorised this.

## ENVIRONMENT
- Working directory: `{directory}`
- Allowed root: `{root}` — never try to act outside it
- Protected locations (always blocked, do not attempt): {denied}
- Shell commands, file edits, and web fetches need the owner's approval; a \
button appears in Slack for each one. Reads, searches, and listings run \
immediately.

## HOW TO WORK
- Do what is asked. Prefer the smallest, safest command that answers the question.
- Batch independent commands only when the owner clearly wants them all.
- If an approval is denied, stop and ask what to do instead; do not retry or \
work around it.
- Never print secrets, tokens, or credential file contents, even if a tool \
returns them.
- To change the working directory the owner uses the `cd <path>` command in \
Slack; tell them that if they ask you to cd.
- To change the model the owner uses the `model <name>` command in Slack \
(`model list <text>` browses, `model reset` restores the default); tell them \
that if they ask you to switch models.

## REPLY STYLE
- Slack-flavoured Markdown. Short. Lead with the result, then anything the owner \
must decide.
- Put command output in a code block, trimmed to what matters.
- No preamble, no restating the request.
"""

CD_PATTERN = re.compile(r"^\s*cd(?:\s+(?P<path>.+?))?\s*$", re.IGNORECASE)
PWD_PATTERN = re.compile(r"^\s*pwd\s*$", re.IGNORECASE)


@dataclass
class OperatorResult:
    final_output: str
    session_id: str | None
    directory: str
    model: ModelRef | None = None

    def to_input_list(self) -> list:
        state = {
            "mode": "operator",
            "session_id": self.session_id,
            "directory": self.directory,
        }
        if self.model:
            state["model"] = str(self.model)
        return [state]


_policy: OperatorPolicy | None = None


def get_policy() -> OperatorPolicy:
    global _policy
    if _policy is None:
        _policy = OperatorPolicy.from_env()
    return _policy


def is_operator(user_id: str | None) -> bool:
    return get_policy().is_operator(user_id)


def run_operator(
    text: str,
    deps: CaseyDeps,
    history: list | None,
    model: ModelRef | None = None,
) -> OperatorResult:
    policy = get_policy()
    state = _state_from(history)
    directory = Path(state.get("directory") or policy.start_directory)
    session_id = state.get("session_id")

    match = CD_PATTERN.match(text)
    if match:
        return _change_directory(policy, match.group("path"), directory, model)
    if PWD_PATTERN.match(text):
        return OperatorResult(f"`{directory}`", session_id, str(directory), model)

    client = get_client()
    approval_router.start(client)

    if session_id is None:
        session_id = client.create_session(
            title=f"Operator {deps.channel_id} {deps.thread_ts}",
            directory=str(directory),
            permission=policy.permission_rules(directory),
        )
        logger.info("Created operator session %s in %s", session_id, directory)

    approval_router.register(
        session_id,
        client=deps.client,
        channel_id=deps.channel_id,
        thread_ts=deps.thread_ts,
        user_id=deps.user_id,
        directory=str(directory),
    )
    try:
        message = client.prompt_message(
            session_id,
            text=text,
            system=OPERATOR_SYSTEM_PROMPT.format(
                directory=directory,
                root=policy.root,
                denied=policy.describe_denied(),
            ),
            directory=str(directory),
            timeout=_turn_timeout(),
            model=model,
        )
    except Exception:
        _abort_quietly(client, session_id, str(directory))
        raise
    finally:
        approval_router.unregister(session_id)

    raise_for_message_error(message)
    return OperatorResult(_render(message), session_id, str(directory), model)


def _change_directory(
    policy: OperatorPolicy,
    requested: str | None,
    current: Path,
    model: ModelRef | None = None,
) -> OperatorResult:
    target = requested or str(policy.start_directory)
    try:
        resolved = policy.resolve_directory(target, current)
    except OperatorPolicyError as error:
        return OperatorResult(
            f":no_entry: {error}\nStill in `{current}`.", None, str(current), model
        )
    return OperatorResult(
        f"Working directory is now `{resolved}`. New OpenCode session will start "
        f"with your next message.",
        None,
        str(resolved),
        model,
    )


def _render(message: dict) -> str:
    text_chunks = []
    tool_lines = []
    denied = False
    for part in message.get("parts", []):
        kind = part.get("type")
        if kind == "text" and part.get("text"):
            text_chunks.append(part["text"])
        elif kind == "tool":
            tool_lines.append(_tool_line(part))
            if "rejected permission" in str(part.get("state", {}).get("error", "")):
                denied = True

    output = "\n".join(text_chunks).strip()
    if not output:
        output = (
            "_Stopped: an action was denied. Tell me how you'd like to proceed._"
            if denied
            else "_(no text reply)_"
        )
    if tool_lines:
        shown = tool_lines[:MAX_TOOL_LINES]
        if len(tool_lines) > MAX_TOOL_LINES:
            shown.append(f"… {len(tool_lines) - MAX_TOOL_LINES} more")
        output += "\n\n_Tools:_\n" + "\n".join(shown)
    return output


def _tool_line(part: dict) -> str:
    state = part.get("state", {})
    status = state.get("status", "?")
    title = state.get("title") or _summarise_input(state.get("input", {}))
    marker = {"completed": "✓", "error": "✗"}.get(status, "…")
    return f"{marker} `{part.get('tool')}` {title}"


def _summarise_input(tool_input: dict) -> str:
    for key in ("command", "filePath", "pattern", "path", "url"):
        if tool_input.get(key):
            return str(tool_input[key])
    return ""


def _state_from(history: list | None) -> dict:
    if not history:
        return {}
    entry = history[0]
    return entry if entry.get("mode") == "operator" else {}


def _turn_timeout() -> float:
    return float(
        os.environ.get("CASEY_OPERATOR_TURN_TIMEOUT", DEFAULT_TURN_TIMEOUT_SECONDS)
    )


def _abort_quietly(client: OpenCodeClient, session_id: str, directory: str) -> None:
    try:
        client.abort_session(session_id, directory=directory)
    except (httpx.HTTPError, OpenCodeUnavailable):
        logger.warning("Could not abort operator session %s", session_id)
