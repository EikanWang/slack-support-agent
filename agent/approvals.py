import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from slack_sdk import WebClient

from agent.opencode_client import OpenCodeClient

logger = logging.getLogger(__name__)

APPROVE_ACTION_ID = "operator_approve"
APPROVE_ALWAYS_ACTION_ID = "operator_approve_always"
DENY_ACTION_ID = "operator_deny"
RESPONSE_BY_ACTION = {
    APPROVE_ACTION_ID: "once",
    APPROVE_ALWAYS_ACTION_ID: "always",
    DENY_ACTION_ID: "reject",
}
VERDICT_BY_RESPONSE = {
    "once": "Approved",
    "always": "Approved for this session",
    "reject": "Denied",
}
RECONNECT_DELAY_SECONDS = 3.0
DEFAULT_AUDIT_LOG = "logs/operator-audit.log"


@dataclass
class ThreadTarget:
    client: WebClient
    channel_id: str
    thread_ts: str
    user_id: str
    directory: str | None = None


@dataclass
class PendingApproval:
    session_id: str
    permission_id: str
    target: ThreadTarget
    summary: str
    directory: str | None = None
    message_ts: str | None = None
    always_patterns: list[str] = field(default_factory=list)
    asked_at: float = field(default_factory=time.time)


class ApprovalRouter:
    def __init__(self):
        self._lock = threading.Lock()
        self._targets: dict[str, ThreadTarget] = {}
        self._pending: dict[str, PendingApproval] = {}
        self._thread: threading.Thread | None = None
        self._opencode: OpenCodeClient | None = None

    def start(self, opencode: OpenCodeClient) -> None:
        with self._lock:
            self._opencode = opencode
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._listen_forever, name="opencode-approvals", daemon=True
            )
            self._thread.start()

    def register(
        self,
        session_id: str,
        client: WebClient,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        directory: str | None = None,
    ) -> None:
        with self._lock:
            self._targets[session_id] = ThreadTarget(
                client, channel_id, thread_ts, user_id, directory
            )

    def unregister(self, session_id: str) -> None:
        with self._lock:
            self._targets.pop(session_id, None)

    def reply(
        self, session_id: str, permission_id: str, response: str, actor_user_id: str
    ) -> PendingApproval | None:
        with self._lock:
            pending = self._pending.pop(permission_id, None)
            opencode = self._opencode
            target = self._targets.get(session_id)
        if opencode is None:
            raise RuntimeError("Approval router not started")
        directory = (
            pending.directory if pending else (target.directory if target else None)
        )
        opencode.reply_permission(
            session_id, permission_id, response, directory=directory
        )
        audit(
            "reply",
            session_id=session_id,
            permission_id=permission_id,
            response=response,
            actor=actor_user_id,
            summary=pending.summary if pending else None,
        )
        return pending

    def handle_event(self, event: dict) -> None:
        kind = event.get("type")
        properties = event.get("properties", {})
        if kind == "permission.asked":
            self._on_asked(properties, event.get("directory"))
        elif kind == "permission.replied":
            self._on_replied(properties)

    def _on_asked(self, request: dict, directory: str | None) -> None:
        session_id = request.get("sessionID", "")
        with self._lock:
            target = self._targets.get(session_id)
        if target is None:
            return

        summary = summarise_request(request)
        always_patterns = list(request.get("always") or [])
        pending = PendingApproval(
            session_id=session_id,
            permission_id=request["id"],
            target=target,
            summary=summary,
            directory=directory or target.directory,
            always_patterns=always_patterns,
        )
        audit(
            "asked",
            session_id=session_id,
            permission_id=request["id"],
            permission=request.get("permission"),
            summary=summary,
            user=target.user_id,
        )
        try:
            posted = target.client.chat_postMessage(
                channel=target.channel_id,
                thread_ts=target.thread_ts,
                text=f"Approval needed: {summary}",
                blocks=build_approval_blocks(
                    request.get("permission", "tool"),
                    summary,
                    session_id,
                    request["id"],
                    always_patterns=always_patterns,
                ),
            )
            pending.message_ts = posted["ts"]
        except Exception:
            logger.exception("Failed to post approval request to Slack")
        with self._lock:
            self._pending[request["id"]] = pending

    def _on_replied(self, properties: dict) -> None:
        with self._lock:
            pending = self._pending.pop(properties.get("requestID", ""), None)
        if pending is None or pending.message_ts is None:
            return
        verdict = VERDICT_BY_RESPONSE.get(properties.get("reply"), "Denied")
        try:
            pending.target.client.chat_update(
                channel=pending.target.channel_id,
                ts=pending.message_ts,
                text=f"{verdict}: {pending.summary}",
                blocks=build_resolved_blocks(verdict, pending.summary),
            )
        except Exception:
            logger.exception("Failed to update approval message")

    def _listen_forever(self) -> None:
        while True:
            opencode = self._opencode
            if opencode is None:
                time.sleep(RECONNECT_DELAY_SECONDS)
                continue
            try:
                logger.info("Approval router connected to OpenCode events")
                for event in opencode.iter_events():
                    try:
                        self.handle_event(event)
                    except Exception:
                        logger.exception("Error handling OpenCode event")
            except (httpx.HTTPError, OSError) as error:
                logger.warning("OpenCode event stream dropped: %s", error)
            time.sleep(RECONNECT_DELAY_SECONDS)


def summarise_request(request: dict) -> str:
    permission = request.get("permission", "tool")
    metadata = request.get("metadata", {}) or {}
    if permission == "bash" and metadata.get("command"):
        return f"`{permission}` → `{metadata['command']}`"
    patterns = request.get("patterns") or []
    if patterns:
        joined = ", ".join(f"`{p}`" for p in patterns[:3])
        if len(patterns) > 3:
            joined += f" (+{len(patterns) - 3})"
        return f"`{permission}` → {joined}"
    return f"`{permission}`"


def build_approval_blocks(
    permission: str,
    summary: str,
    session_id: str,
    permission_id: str,
    always_patterns: list[str] | None = None,
) -> list:
    value = json.dumps({"session_id": session_id, "permission_id": permission_id})
    text = f":lock: *Approval needed* ({permission})\n{summary}"
    if always_patterns:
        remembered = ", ".join(f"`{p}`" for p in always_patterns[:3])
        if len(always_patterns) > 3:
            remembered += f" (+{len(always_patterns) - 3})"
        text += f"\n_Always_ also allows {remembered} for the rest of this thread."
    elements = [
        {
            "type": "button",
            "action_id": APPROVE_ACTION_ID,
            "style": "primary",
            "text": {"type": "plain_text", "text": "Approve"},
            "value": value,
        },
    ]
    if always_patterns:
        elements.append(
            {
                "type": "button",
                "action_id": APPROVE_ALWAYS_ACTION_ID,
                "text": {"type": "plain_text", "text": "Always"},
                "value": value,
            }
        )
    elements.append(
        {
            "type": "button",
            "action_id": DENY_ACTION_ID,
            "style": "danger",
            "text": {"type": "plain_text", "text": "Deny"},
            "value": value,
        }
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "actions", "elements": elements},
    ]


def build_resolved_blocks(verdict: str, summary: str, actor: str | None = None) -> list:
    emoji = ":no_entry:" if verdict == "Denied" else ":white_check_mark:"
    by = f" by <@{actor}>" if actor else ""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} *{verdict}*{by}\n{summary}"},
        }
    ]


def audit(event: str, **fields) -> None:
    path = Path(os.environ.get("CASEY_AUDIT_LOG", DEFAULT_AUDIT_LOG))
    record = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        **{k: v for k, v in fields.items() if v is not None},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        logger.exception("Could not write audit log at %s", path)


approval_router = ApprovalRouter()
