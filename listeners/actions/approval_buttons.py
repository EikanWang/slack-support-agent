import json
from logging import Logger

from slack_bolt import Ack, BoltContext
from slack_sdk import WebClient

from agent.approvals import (
    APPROVE_ACTION_ID,
    approval_router,
    audit,
    build_resolved_blocks,
)
from agent.operator import is_operator


def handle_approval_button(
    ack: Ack, body: dict, client: WebClient, context: BoltContext, logger: Logger
):
    """Relay Approve/Deny clicks on operator tool requests to OpenCode."""
    ack()

    action = body["actions"][0]
    payload = json.loads(action["value"])
    session_id = payload["session_id"]
    permission_id = payload["permission_id"]
    user_id = context.user_id
    channel_id = context.channel_id
    message_ts = body["message"]["ts"]

    if not is_operator(user_id):
        audit(
            "rejected_click",
            session_id=session_id,
            permission_id=permission_id,
            actor=user_id,
        )
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=body["message"].get("thread_ts") or message_ts,
            text="Only the machine owner can approve or deny operator actions.",
        )
        return

    response = "once" if action["action_id"] == APPROVE_ACTION_ID else "reject"
    verdict = "Approved" if response == "once" else "Denied"
    try:
        pending = approval_router.reply(session_id, permission_id, response, user_id)
        summary = pending.summary if pending else "request"
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=f"{verdict}: {summary}",
            blocks=build_resolved_blocks(verdict, summary, actor=user_id),
        )
    except Exception:
        logger.exception("Failed to relay approval to OpenCode")
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            thread_ts=body["message"].get("thread_ts") or message_ts,
            text=":warning: Could not deliver that decision to OpenCode. "
            "The request may have expired or the server restarted.",
        )
