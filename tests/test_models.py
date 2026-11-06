import time
from unittest.mock import Mock

import pytest

from agent import models, router
from agent.deps import CaseyDeps
from agent.models import (
    ModelCatalogue,
    ModelCommand,
    ModelCommandError,
    handle_model_command,
    parse_model_command,
    resolve,
    search,
)
from agent.opencode_client import ModelRef, OpenCodeClient
from agent.support_agent import CaseyResult

CATALOGUE = [
    ModelRef("anthropic", "claude-sonnet-4-5"),
    ModelRef("anthropic", "claude-haiku-4-5"),
    ModelRef("openai", "gpt-5"),
    ModelRef("github-copilot", "claude-sonnet-4-5"),
]


@pytest.fixture
def client():
    fake = Mock(OpenCodeClient)
    fake.model = ModelRef("anthropic", "claude-sonnet-4-5")
    fake.available_models.return_value = list(CATALOGUE)
    return fake


@pytest.fixture(autouse=True)
def fresh_catalogue(monkeypatch):
    monkeypatch.setattr(models, "catalogue", ModelCatalogue())


class TestParsing:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("model", None),
            ("  Model  ", None),
            ("/model", None),
            ("models", None),
            ("model gpt-5", "gpt-5"),
            ("model list anthropic", "list anthropic"),
            ("model reset", "reset"),
        ],
    )
    def test_recognises_model_commands(self, text, expected):
        assert parse_model_command(text) == ModelCommand(argument=expected)

    @pytest.mark.parametrize(
        "text", ["modelling clay", "what model are you", "remodel the kitchen", "hi"]
    )
    def test_ignores_other_text(self, text):
        assert parse_model_command(text) is None


class TestSearchAndResolve:
    def test_search_requires_every_term(self):
        assert search(CATALOGUE, "anthropic haiku") == [
            ModelRef("anthropic", "claude-haiku-4-5")
        ]

    def test_search_splits_on_slash(self):
        assert search(CATALOGUE, "openai/gpt") == [ModelRef("openai", "gpt-5")]

    def test_resolves_exact_qualified_name(self):
        assert resolve(CATALOGUE, "openai/gpt-5") == ModelRef("openai", "gpt-5")

    def test_resolves_unique_substring(self):
        assert resolve(CATALOGUE, "haiku") == ModelRef("anthropic", "claude-haiku-4-5")

    def test_prefers_exact_model_id_over_substring(self):
        catalogue = [ModelRef("openai", "gpt-5"), ModelRef("openai", "gpt-5-mini")]

        assert resolve(catalogue, "gpt-5") == ModelRef("openai", "gpt-5")

    def test_rejects_ambiguous_query(self):
        with pytest.raises(ModelCommandError, match="matches 2 models"):
            resolve(CATALOGUE, "claude-sonnet-4-5")

    def test_rejects_unknown_model(self):
        with pytest.raises(ModelCommandError, match="No model matches"):
            resolve(CATALOGUE, "llama")


class TestCommand:
    def test_bare_command_reports_default(self, client):
        reply, model, changed = handle_model_command(None, client, None)

        assert "`anthropic/claude-sonnet-4-5`" in reply and "_(default)_" in reply
        assert model is None and changed is False

    def test_bare_command_reports_override(self, client):
        current = ModelRef("openai", "gpt-5")

        reply, model, changed = handle_model_command(None, client, current)

        assert "`openai/gpt-5`" in reply and "_(default)_" not in reply
        assert model == current and changed is False

    def test_switches_model(self, client):
        reply, model, changed = handle_model_command("haiku", client, None)

        assert model == ModelRef("anthropic", "claude-haiku-4-5")
        assert changed is True and "Switched to" in reply

    def test_switching_to_current_model_is_a_no_op(self, client):
        reply, model, changed = handle_model_command("haiku", client, None)
        reply, model, changed = handle_model_command("haiku", client, model)

        assert changed is False and "Already using" in reply

    def test_switching_to_the_default_clears_the_override(self, client):
        reply, model, changed = handle_model_command(
            "anthropic/claude-sonnet-4-5", client, ModelRef("openai", "gpt-5")
        )

        assert model is None and changed is True
        assert "Back to the default model" in reply

    def test_reset_clears_override(self, client):
        reply, model, changed = handle_model_command(
            "reset", client, ModelRef("openai", "gpt-5")
        )

        assert model is None and changed is True
        assert "`anthropic/claude-sonnet-4-5`" in reply

    def test_reset_without_override_is_a_no_op(self, client):
        reply, model, changed = handle_model_command("reset", client, None)

        assert model is None and changed is False and "Already on the default" in reply

    def test_list_filters_catalogue(self, client):
        reply, _, changed = handle_model_command("list anthropic", client, None)

        assert "`anthropic/claude-haiku-4-5`" in reply
        assert "openai/gpt-5" not in reply
        assert changed is False

    def test_list_without_query_shows_everything(self, client):
        reply, _, _ = handle_model_command("list", client, None)

        assert "4 models available" in reply

    def test_list_truncates_long_results(self, client):
        client.available_models.return_value = [
            ModelRef("openai", f"gpt-{index}") for index in range(40)
        ]

        reply, _, _ = handle_model_command("list", client, None)

        assert "…and 25 more." in reply

    def test_catalogue_is_cached_across_calls(self, client):
        handle_model_command("list", client, None)
        handle_model_command("list", client, None)

        client.available_models.assert_called_once()

    def test_catalogue_fetches_once_under_concurrency(self, client):
        import threading

        def slow_fetch():
            time.sleep(0.05)
            return list(CATALOGUE)

        client.available_models.side_effect = slow_fetch
        cache = ModelCatalogue()
        threads = [
            threading.Thread(target=cache.models, args=(client,)) for _ in range(5)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert not any(thread.is_alive() for thread in threads)
        assert client.available_models.call_count == 1

    def test_unreachable_server_raises_command_error(self, client):
        client.available_models.side_effect = OSError("boom")

        with pytest.raises(ModelCommandError, match="Could not reach"):
            handle_model_command("list", client, None)


class TestRouting:
    def _deps(self):
        deps = Mock(CaseyDeps)
        deps.user_id = "U1"
        return deps

    def test_model_command_does_not_reach_an_agent(self, monkeypatch, client):
        monkeypatch.setattr(router, "get_client", lambda: client)
        monkeypatch.setattr(router, "is_operator", lambda _: False)
        monkeypatch.setattr(
            router, "run_casey", lambda *a, **k: pytest.fail("agent was called")
        )

        result = router.run_agent("model haiku", self._deps(), None)

        assert result.to_input_list() == [{"model": "anthropic/claude-haiku-4-5"}]

    def test_switch_preserves_existing_thread_state(self, monkeypatch, client):
        monkeypatch.setattr(router, "get_client", lambda: client)
        monkeypatch.setattr(router, "is_operator", lambda _: False)
        history = [{"mode": "operator", "session_id": "ses_1", "directory": "/w"}]

        result = router.run_agent("model haiku", self._deps(), history)

        assert result.to_input_list() == [
            {
                "mode": "operator",
                "session_id": "ses_1",
                "directory": "/w",
                "model": "anthropic/claude-haiku-4-5",
            }
        ]

    def test_reset_removes_model_from_state(self, monkeypatch, client):
        monkeypatch.setattr(router, "get_client", lambda: client)
        monkeypatch.setattr(router, "is_operator", lambda _: False)
        history = [{"session_id": "ses_1", "model": "openai/gpt-5"}]

        result = router.run_agent("model reset", self._deps(), history)

        assert result.to_input_list() == [{"session_id": "ses_1"}]

    def test_failed_switch_leaves_state_untouched(self, monkeypatch, client):
        monkeypatch.setattr(router, "get_client", lambda: client)
        monkeypatch.setattr(router, "is_operator", lambda _: False)
        history = [{"session_id": "ses_1", "model": "openai/gpt-5"}]

        result = router.run_agent("model nope", self._deps(), history)

        assert ":no_entry:" in result.final_output
        assert result.to_input_list() == history

    def test_thread_model_is_passed_to_the_agent(self, monkeypatch, client):
        monkeypatch.setattr(router, "is_operator", lambda _: False)
        seen = {}

        def fake_casey(text, deps, history, model=None):
            seen["model"] = model
            return CaseyResult("ok", "ses_1", model)

        monkeypatch.setattr(router, "run_casey", fake_casey)
        history = [{"session_id": "ses_1", "model": "openai/gpt-5"}]

        result = router.run_agent("hello", self._deps(), history)

        assert seen["model"] == ModelRef("openai", "gpt-5")
        assert result.to_input_list() == [
            {"session_id": "ses_1", "model": "openai/gpt-5"}
        ]
