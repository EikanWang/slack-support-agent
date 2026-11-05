from pathlib import Path
from unittest.mock import Mock

import pytest
from slack_sdk import WebClient

from agent import operator, router
from agent.approvals import (
    APPROVE_ACTION_ID,
    APPROVE_ALWAYS_ACTION_ID,
    DENY_ACTION_ID,
    ApprovalRouter,
    build_approval_blocks,
    summarise_request,
)
from agent.deps import CaseyDeps
from agent.opencode_client import OpenCodeClient
from agent.operator import OperatorResult, run_operator
from agent.operator_policy import OperatorPolicy, OperatorPolicyError


@pytest.fixture
def sandbox(tmp_path: Path) -> dict:
    root = tmp_path / "home"
    (root / "projects" / "app").mkdir(parents=True)
    (root / ".ssh").mkdir()
    (root / ".config").mkdir()
    creds = tmp_path / "creds.sh"
    creds.write_text("export TOKEN=secret\n")
    (tmp_path / "outside").mkdir()
    return {"root": root, "creds": creds, "tmp": tmp_path}


@pytest.fixture
def policy(sandbox) -> OperatorPolicy:
    return OperatorPolicy.from_env(
        {
            "CASEY_OPERATOR_USER_IDS": "UOWNER, UALSO",
            "CASEY_OPERATOR_ROOT": str(sandbox["root"]),
            "CASEY_OPERATOR_HOME": str(sandbox["root"]),
            "CASEY_CREDENTIALS_FILE": str(sandbox["creds"]),
        }
    )


@pytest.fixture
def whole_fs_policy(sandbox) -> OperatorPolicy:
    return OperatorPolicy.from_env(
        {
            "CASEY_OPERATOR_USER_IDS": "UOWNER",
            "CASEY_OPERATOR_ROOT": "/",
            "CASEY_OPERATOR_HOME": str(sandbox["root"]),
            "CASEY_CREDENTIALS_FILE": str(sandbox["creds"]),
        }
    )


class TestOperatorPolicy:
    def test_disabled_without_user_ids(self):
        policy = OperatorPolicy.from_env({})

        assert not policy.enabled
        assert not policy.is_operator("UANY")

    def test_requires_root_when_enabled(self):
        with pytest.raises(OperatorPolicyError, match="CASEY_OPERATOR_ROOT"):
            OperatorPolicy.from_env({"CASEY_OPERATOR_USER_IDS": "U1"})

    def test_operator_allowlist(self, policy):
        assert policy.is_operator("UOWNER")
        assert policy.is_operator("UALSO")
        assert not policy.is_operator("USTRANGER")
        assert not policy.is_operator(None)

    def test_resolves_relative_to_current_directory(self, policy, sandbox):
        current = sandbox["root"] / "projects"

        resolved = policy.resolve_directory("app", current)

        assert resolved == sandbox["root"] / "projects" / "app"

    def test_rejects_outside_root(self, policy, sandbox):
        with pytest.raises(OperatorPolicyError, match="outside the allowed root"):
            policy.resolve_directory(str(sandbox["tmp"] / "outside"))

    def test_rejects_escaping_via_dotdot(self, policy, sandbox):
        with pytest.raises(OperatorPolicyError, match="outside the allowed root"):
            policy.resolve_directory("../outside", sandbox["root"])

    def test_rejects_protected_locations(self, policy, sandbox):
        with pytest.raises(OperatorPolicyError, match="protected"):
            policy.resolve_directory(".ssh", sandbox["root"])

    def test_rejects_missing_directory(self, policy, sandbox):
        with pytest.raises(OperatorPolicyError, match="not a directory"):
            policy.resolve_directory("nope", sandbox["root"])

    def test_credentials_file_is_protected(self, policy, sandbox):
        assert sandbox["creds"].resolve() in policy.denied_paths

    def test_denied_names_outside_root_are_dropped(self, policy):
        assert all(
            str(p).startswith(str(policy.root)) or p.name == "creds.sh"
            for p in policy.denied_paths
        )
        assert not any(str(p).startswith("/etc") for p in policy.denied_paths)

    def test_whole_filesystem_root(self, whole_fs_policy, sandbox):
        policy = whole_fs_policy

        assert policy.start_directory == sandbox["root"]
        assert policy.resolve_directory("/tmp") == Path("/tmp").resolve()
        assert policy.resolve_directory("..", sandbox["root"]) == sandbox["tmp"]
        with pytest.raises(OperatorPolicyError, match="protected"):
            policy.resolve_directory(".ssh", sandbox["root"])
        with pytest.raises(OperatorPolicyError, match="protected"):
            policy.resolve_directory("/root")
        assert Path("/etc/ssh") in policy.denied_paths

    def test_whole_filesystem_rules(self, whole_fs_policy, sandbox):
        rules = {
            (r.permission, r.pattern, r.action)
            for r in whole_fs_policy.permission_rules(sandbox["root"])
        }

        assert ("external_directory", "/**", "allow") in rules
        assert ("bash", "*.ssh*", "deny") in rules
        assert ("bash", "*/etc/shadow*", "deny") in rules
        assert ("bash", "*/etc/ssh/*", "deny") in rules
        assert ("read", "/etc/shadow", "deny") in rules

    def test_permission_rules_gate_and_deny(self, policy, sandbox):
        directory = sandbox["root"] / "projects" / "app"

        rules = policy.permission_rules(directory)
        as_tuples = {(r.permission, r.pattern, r.action) for r in rules}

        assert ("bash", "*", "ask") in as_tuples
        assert ("edit", "*", "ask") in as_tuples
        assert ("external_directory", "*", "deny") in as_tuples
        assert ("external_directory", f"{sandbox['root']}/**", "allow") in as_tuples
        assert ("read", str(sandbox["creds"]), "deny") in as_tuples
        assert ("read", "../../../creds.sh", "deny") in as_tuples
        assert ("read", f"{sandbox['root']}/.ssh/**", "deny") in as_tuples
        assert ("bash", "*.ssh*", "deny") in as_tuples
        assert ("bash", "*creds.sh*", "deny") in as_tuples


class TestRunOperator:
    def setup_method(self):
        self.fake_opencode = Mock(OpenCodeClient)
        self.fake_opencode.create_session.return_value = "ses_op"
        self.fake_opencode.prompt_message.return_value = {
            "info": {},
            "parts": [
                {"type": "step-start"},
                {
                    "type": "tool",
                    "tool": "bash",
                    "state": {"status": "completed", "title": "ls", "input": {}},
                },
                {"type": "text", "text": "Two files."},
            ],
        }
        self.fake_router = Mock()
        self.deps = CaseyDeps(
            client=Mock(WebClient),
            user_id="UOWNER",
            channel_id="C1",
            thread_ts="1.0",
            message_ts="1.0",
        )

    def _patch(self, monkeypatch, policy):
        monkeypatch.setattr(operator, "_policy", policy)
        monkeypatch.setattr(operator, "get_client", lambda: self.fake_opencode)
        monkeypatch.setattr(operator, "approval_router", self.fake_router)

    def test_creates_session_rooted_at_root_with_rules(self, monkeypatch, policy):
        self._patch(monkeypatch, policy)

        result = run_operator("list files", self.deps, history=None)

        kwargs = self.fake_opencode.create_session.call_args.kwargs
        assert kwargs["directory"] == str(policy.root)
        assert any(
            r.permission == "bash" and r.action == "ask" for r in kwargs["permission"]
        )
        self.fake_router.start.assert_called_once_with(self.fake_opencode)
        self.fake_router.register.assert_called_once()
        self.fake_router.unregister.assert_called_once_with("ses_op")
        assert result.session_id == "ses_op"
        assert "Two files." in result.final_output
        assert "✓ `bash` ls" in result.final_output
        assert result.to_input_list()[0]["mode"] == "operator"

    def test_cd_changes_directory_without_calling_model(
        self, monkeypatch, policy, sandbox
    ):
        self._patch(monkeypatch, policy)

        result = run_operator("cd projects/app", self.deps, history=None)

        self.fake_opencode.prompt_message.assert_not_called()
        assert result.directory == str(sandbox["root"] / "projects" / "app")
        assert result.session_id is None

    def test_cd_to_protected_path_is_refused(self, monkeypatch, policy, sandbox):
        self._patch(monkeypatch, policy)
        history = [
            {
                "mode": "operator",
                "session_id": "ses_x",
                "directory": str(sandbox["root"]),
            }
        ]

        result = run_operator("cd .ssh", self.deps, history=history)

        assert ":no_entry:" in result.final_output
        assert result.directory == str(sandbox["root"])

    def test_pwd_reports_directory(self, monkeypatch, policy, sandbox):
        self._patch(monkeypatch, policy)
        directory = str(sandbox["root"] / "projects")
        history = [{"mode": "operator", "session_id": "ses_x", "directory": directory}]

        result = run_operator("pwd", self.deps, history=history)

        assert result.final_output == f"`{directory}`"

    def test_reuses_session_and_passes_directory(self, monkeypatch, policy, sandbox):
        self._patch(monkeypatch, policy)
        directory = str(sandbox["root"] / "projects")
        history = [
            {"mode": "operator", "session_id": "ses_keep", "directory": directory}
        ]

        run_operator("git status", self.deps, history=history)

        self.fake_opencode.create_session.assert_not_called()
        kwargs = self.fake_opencode.prompt_message.call_args.kwargs
        assert kwargs["directory"] == directory
        assert "tools" not in kwargs

    def test_aborts_session_when_prompt_fails(self, monkeypatch, policy):
        self._patch(monkeypatch, policy)
        self.fake_opencode.prompt_message.side_effect = TimeoutError("slow")

        with pytest.raises(TimeoutError):
            run_operator("sleep forever", self.deps, history=None)

        self.fake_opencode.abort_session.assert_called_once_with(
            "ses_op", directory=str(policy.start_directory)
        )
        self.fake_router.unregister.assert_called_once_with("ses_op")


class TestRouter:
    def test_operator_goes_to_operator_mode(self, monkeypatch, policy):
        monkeypatch.setattr(operator, "_policy", policy)
        calls = []
        monkeypatch.setattr(
            router,
            "run_operator",
            lambda *a: calls.append("operator") or OperatorResult("", None, ""),
        )
        monkeypatch.setattr(router, "run_casey", lambda *a: calls.append("casey"))
        deps = Mock(CaseyDeps)

        deps.user_id = "UOWNER"
        router.run_agent("hi", deps, None)
        deps.user_id = "USTRANGER"
        router.run_agent("hi", deps, None)

        assert calls == ["operator", "casey"]


class TestApprovalRouter:
    def _request(self, **overrides):
        request = {
            "id": "per_1",
            "sessionID": "ses_op",
            "permission": "bash",
            "patterns": ["ls"],
            "metadata": {"command": "ls -la"},
            "always": ["ls *"],
        }
        request.update(overrides)
        return request

    def test_summarises_bash_with_command(self):
        assert summarise_request(self._request()) == "`bash` → `ls -la`"

    def test_summarises_edit_with_patterns(self):
        request = self._request(
            permission="edit", patterns=["a.py", "b.py"], metadata={}
        )

        assert summarise_request(request) == "`edit` → `a.py`, `b.py`"

    def test_posts_buttons_only_for_registered_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASEY_AUDIT_LOG", str(tmp_path / "audit.log"))
        approvals = ApprovalRouter()
        slack = Mock(WebClient)
        slack.chat_postMessage.return_value = {"ts": "9.9"}

        approvals.handle_event(
            {
                "type": "permission.asked",
                "properties": self._request(sessionID="ses_unknown"),
            }
        )
        slack.chat_postMessage.assert_not_called()

        approvals.register("ses_op", slack, "C1", "1.0", "UOWNER")
        approvals.handle_event(
            {"type": "permission.asked", "properties": self._request()}
        )

        kwargs = slack.chat_postMessage.call_args.kwargs
        assert kwargs["channel"] == "C1" and kwargs["thread_ts"] == "1.0"
        actions = next(b for b in kwargs["blocks"] if b["type"] == "actions")
        assert [e["action_id"] for e in actions["elements"]] == [
            APPROVE_ACTION_ID,
            APPROVE_ALWAYS_ACTION_ID,
            DENY_ACTION_ID,
        ]
        assert (tmp_path / "audit.log").read_text().count('"event": "asked"') == 1

    def test_omits_always_button_when_nothing_can_be_remembered(self, monkeypatch):
        monkeypatch.setenv("CASEY_AUDIT_LOG", "/dev/null")
        approvals = ApprovalRouter()
        slack = Mock(WebClient)
        slack.chat_postMessage.return_value = {"ts": "9.9"}
        approvals.register("ses_op", slack, "C1", "1.0", "UOWNER")

        approvals.handle_event(
            {"type": "permission.asked", "properties": self._request(always=[])}
        )

        kwargs = slack.chat_postMessage.call_args.kwargs
        actions = next(b for b in kwargs["blocks"] if b["type"] == "actions")
        assert [e["action_id"] for e in actions["elements"]] == [
            APPROVE_ACTION_ID,
            DENY_ACTION_ID,
        ]

    def test_reply_relays_to_opencode_and_audits(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASEY_AUDIT_LOG", str(tmp_path / "audit.log"))
        approvals = ApprovalRouter()
        opencode = Mock(OpenCodeClient)
        approvals._opencode = opencode
        slack = Mock(WebClient)
        slack.chat_postMessage.return_value = {"ts": "9.9"}
        approvals.register("ses_op", slack, "C1", "1.0", "UOWNER", directory="/w")
        approvals.handle_event(
            {
                "type": "permission.asked",
                "properties": self._request(),
                "directory": "/w",
            }
        )

        pending = approvals.reply("ses_op", "per_1", "reject", actor_user_id="UOWNER")

        opencode.reply_permission.assert_called_once_with(
            "ses_op", "per_1", "reject", directory="/w"
        )
        assert pending.summary == "`bash` → `ls -la`"
        log = (tmp_path / "audit.log").read_text()
        assert '"event": "reply"' in log and '"response": "reject"' in log

    def test_replied_event_updates_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASEY_AUDIT_LOG", str(tmp_path / "audit.log"))
        approvals = ApprovalRouter()
        slack = Mock(WebClient)
        slack.chat_postMessage.return_value = {"ts": "9.9"}
        approvals.register("ses_op", slack, "C1", "1.0", "UOWNER")
        approvals.handle_event(
            {"type": "permission.asked", "properties": self._request()}
        )

        approvals.handle_event(
            {
                "type": "permission.replied",
                "properties": {
                    "sessionID": "ses_op",
                    "requestID": "per_1",
                    "reply": "once",
                },
            }
        )

        kwargs = slack.chat_update.call_args.kwargs
        assert kwargs["ts"] == "9.9"
        assert "Approved" in kwargs["text"]

    def test_always_reply_is_relayed_and_audited(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CASEY_AUDIT_LOG", str(tmp_path / "audit.log"))
        approvals = ApprovalRouter()
        opencode = Mock(OpenCodeClient)
        approvals._opencode = opencode
        slack = Mock(WebClient)
        slack.chat_postMessage.return_value = {"ts": "9.9"}
        approvals.register("ses_op", slack, "C1", "1.0", "UOWNER")
        approvals.handle_event(
            {"type": "permission.asked", "properties": self._request()}
        )

        pending = approvals.reply("ses_op", "per_1", "always", actor_user_id="UOWNER")

        opencode.reply_permission.assert_called_once_with(
            "ses_op", "per_1", "always", directory=None
        )
        assert pending.always_patterns == ["ls *"]
        assert '"response": "always"' in (tmp_path / "audit.log").read_text()

    def test_always_reply_from_elsewhere_is_labelled(self, monkeypatch):
        monkeypatch.setenv("CASEY_AUDIT_LOG", "/dev/null")
        approvals = ApprovalRouter()
        slack = Mock(WebClient)
        slack.chat_postMessage.return_value = {"ts": "9.9"}
        approvals.register("ses_op", slack, "C1", "1.0", "UOWNER")
        approvals.handle_event(
            {"type": "permission.asked", "properties": self._request()}
        )

        approvals.handle_event(
            {
                "type": "permission.replied",
                "properties": {"requestID": "per_1", "reply": "always"},
            }
        )

        assert "Approved for this session" in slack.chat_update.call_args.kwargs["text"]

    def test_approval_blocks_carry_ids(self):
        blocks = build_approval_blocks("bash", "`bash` → `ls`", "ses_op", "per_1")

        button = blocks[1]["elements"][0]
        assert '"session_id": "ses_op"' in button["value"]
        assert '"permission_id": "per_1"' in button["value"]

    def test_approval_blocks_explain_what_always_remembers(self):
        blocks = build_approval_blocks(
            "bash", "`bash` → `ls`", "ses_op", "per_1", always_patterns=["ls *"]
        )

        assert "_Always_ also allows `ls *`" in blocks[0]["text"]["text"]
        assert [e["action_id"] for e in blocks[1]["elements"]] == [
            APPROVE_ACTION_ID,
            APPROVE_ALWAYS_ACTION_ID,
            DENY_ACTION_ID,
        ]
