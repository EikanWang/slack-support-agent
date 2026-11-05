import re

from slack_bolt import App

from agent.approvals import RESPONSE_BY_ACTION

from .approval_buttons import handle_approval_button
from .feedback_buttons import handle_feedback_button
from .issue_buttons import handle_issue_button


def register(app: App):
    app.action(re.compile(r"^category_"))(handle_issue_button)
    app.action("feedback")(handle_feedback_button)
    for action_id in RESPONSE_BY_ACTION:
        app.action(action_id)(handle_approval_button)
