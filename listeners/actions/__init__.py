import re

from slack_bolt import App

from agent.approvals import APPROVE_ACTION_ID, DENY_ACTION_ID

from .approval_buttons import handle_approval_button
from .feedback_buttons import handle_feedback_button
from .issue_buttons import handle_issue_button


def register(app: App):
    app.action(re.compile(r"^category_"))(handle_issue_button)
    app.action("feedback")(handle_feedback_button)
    app.action(APPROVE_ACTION_ID)(handle_approval_button)
    app.action(DENY_ACTION_ID)(handle_approval_button)
