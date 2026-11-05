from logging import Logger

from slack_bolt import BoltContext, Say, SayStream, SetStatus
from slack_sdk import WebClient

from agent import CaseyDeps, run_agent
from listeners.views.feedback_builder import build_feedback_blocks
from thread_context import conversation_store


def handle_message(
    client: WebClient,
    context: BoltContext,
    event: dict,
    logger: Logger,
    say: Say,
    say_stream: SayStream,
    set_status: SetStatus,
):
    """Handle messages sent to Casey via DM or in threads the bot is part of."""
    is_issue_submission = (
        event.get("metadata", {}).get("event_type") == "issue_submission"
    )

    if event.get("subtype"):
        return
    if event.get("bot_id") and not is_issue_submission:
        return

    is_dm = event.get("channel_type") == "im"
    is_thread_reply = event.get("thread_ts") is not None

    if is_dm:
        pass
    elif is_thread_reply:
        history = conversation_store.get_history(context.channel_id, event["thread_ts"])
        if history is None:
            return
    else:
        return

    try:
        channel_id = context.channel_id
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]

        if is_issue_submission:
            user_id = event["metadata"]["event_payload"]["user_id"]
        else:
            user_id = context.user_id

        history = conversation_store.get_history(channel_id, thread_ts)

        if is_dm and history is None:
            client.reactions_add(
                channel=channel_id,
                timestamp=event["ts"],
                name="eyes",
            )

        set_status(
            status="Thinking...",
            loading_messages=[
                "Teaching the hamsters to type faster…",
                "Untangling the internet cables…",
                "Consulting the office goldfish…",
                "Polishing up the response just for you…",
                "Convincing the AI to stop overthinking…",
            ],
        )

        deps = CaseyDeps(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=event["ts"],
        )
        result = run_agent(text, deps, history)

        streamer = say_stream()
        streamer.append(markdown_text=result.final_output)
        feedback_blocks = build_feedback_blocks()
        streamer.stop(blocks=feedback_blocks)

        conversation_store.set_history(channel_id, thread_ts, result.to_input_list())

    except Exception as e:
        logger.exception("Failed to handle message")
        say(
            text=f":warning: Something went wrong! ({e})",
            thread_ts=event.get("thread_ts") or event.get("ts"),
        )
