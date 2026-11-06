import json
from unittest.mock import Mock

import httpx
import pytest
from slack_sdk import WebClient

from agent import (
    CASEY_SYSTEM_PROMPT,
    CaseyDeps,
    opencode_client,
    run_casey,
    support_agent,
)
from agent.opencode_client import (
    ModelRef,
    OpenCodeClient,
    OpenCodeCredentialError,
    OpenCodeModelError,
    OpenCodeRequestError,
    OpenCodeUnavailable,
    extract_text,
)


class TestModelRef:
    def test_parses_provider_and_model(self):
        ref = ModelRef.parse("some-provider/some-model-v1")

        assert ref.provider_id == "some-provider"
        assert ref.model_id == "some-model-v1"

    def test_rejects_missing_separator(self):
        with pytest.raises(ValueError):
            ModelRef.parse("gpt-4.1-mini")


class TestExtractText:
    def test_joins_text_parts_and_skips_other_parts(self):
        message = {
            "info": {},
            "parts": [
                {"type": "step-start"},
                {"type": "text", "text": "Hello"},
                {"type": "reasoning", "text": "hidden"},
                {"type": "text", "text": "world"},
            ],
        }

        assert extract_text(message) == "Hello\nworld"

    def test_raises_when_server_reports_error(self):
        message = {
            "info": {"error": {"name": "ProviderError", "data": {"message": "boom"}}},
            "parts": [],
        }

        with pytest.raises(RuntimeError, match="boom"):
            extract_text(message)

    def test_flags_expired_credentials_distinctly(self):
        message = {
            "info": {
                "error": {
                    "name": "APIError",
                    "data": {"message": "The security token included is expired"},
                }
            },
            "parts": [],
        }

        with pytest.raises(OpenCodeCredentialError, match="token expired"):
            extract_text(message)


class TestConnectRetry:
    def test_retries_connect_errors_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(opencode_client, "CONNECT_RETRY_DELAY_SECONDS", 0)
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.path)
            if len(attempts) < 3:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, json={"id": "ses_after_restart"})

        client = OpenCodeClient(
            base_url="http://opencode.test", model=ModelRef("prov", "m")
        )
        client._http = httpx.Client(
            base_url="http://opencode.test", transport=httpx.MockTransport(handler)
        )

        assert client.create_session("t") == "ses_after_restart"
        assert len(attempts) == 3

    def test_gives_up_with_clear_error(self, monkeypatch):
        monkeypatch.setattr(opencode_client, "CONNECT_RETRY_DELAY_SECONDS", 0)
        monkeypatch.setattr(opencode_client, "CONNECT_RETRIES", 2)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        client = OpenCodeClient(
            base_url="http://opencode.test", model=ModelRef("prov", "m")
        )
        client._http = httpx.Client(
            base_url="http://opencode.test", transport=httpx.MockTransport(handler)
        )

        with pytest.raises(OpenCodeUnavailable, match="not reachable"):
            client.create_session("t")


class TestHttpErrors:
    def _client(self, handler):
        client = OpenCodeClient(
            base_url="http://opencode.test", model=ModelRef("prov", "m")
        )
        client._http = httpx.Client(
            base_url="http://opencode.test", transport=httpx.MockTransport(handler)
        )
        return client

    def test_surfaces_the_server_error_body(self):
        def handler(request):
            return httpx.Response(
                404, json={"name": "NotFoundError", "data": {"message": "Session gone"}}
            )

        client = self._client(handler)

        with pytest.raises(
            OpenCodeRequestError, match="NotFoundError Session gone"
        ) as caught:
            client.create_session("t")
        assert caught.value.status_code == 404

    def test_includes_the_error_ref_for_opaque_failures(self):
        def handler(request):
            return httpx.Response(
                500,
                json={
                    "name": "UnknownError",
                    "data": {
                        "message": "Unexpected server error.",
                        "ref": "err_abc123",
                    },
                },
            )

        client = self._client(handler)

        with pytest.raises(OpenCodeRequestError, match="ref err_abc123"):
            client.create_session("t")

    def test_falls_back_to_raw_body_when_not_json(self):
        client = self._client(lambda request: httpx.Response(502, text="bad gateway"))

        with pytest.raises(OpenCodeRequestError, match="bad gateway"):
            client.create_session("t")

    def test_explains_an_unconfigured_model_behind_a_500(self):
        def handler(request):
            if request.url.path == "/config/providers":
                return httpx.Response(
                    200,
                    json={
                        "providers": [
                            {"id": "other", "models": {"m": {}, "spare": {}}},
                        ]
                    },
                )
            return httpx.Response(500, json={"data": {"ref": "err_1"}})

        client = self._client(handler)
        client.create_session = lambda *a, **k: "ses_1"

        with pytest.raises(OpenCodeModelError) as caught:
            client.prompt_message("ses_1", text="hi", system="s")
        message = str(caught.value)
        assert "no model `prov/m` configured" in message
        assert "Did you mean `other/m`?" in message

    def test_keeps_the_http_error_when_the_model_is_fine(self):
        def handler(request):
            if request.url.path == "/config/providers":
                return httpx.Response(
                    200, json={"providers": [{"id": "prov", "models": {"m": {}}}]}
                )
            return httpx.Response(500, json={"data": {"ref": "err_2"}})

        client = self._client(handler)

        with pytest.raises(OpenCodeRequestError, match="ref err_2"):
            client.prompt_message("ses_1", text="hi", system="s")

    def test_lists_available_models(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "providers": [
                        {"id": "b", "models": {"two": {}, "one": {}}},
                        {"id": "a", "models": {"solo": {}}},
                    ]
                },
            )

        client = self._client(handler)

        assert client.available_models() == [
            ModelRef("b", "one"),
            ModelRef("b", "two"),
            ModelRef("a", "solo"),
        ]


class TestOpenCodeClient:
    def test_sends_expected_request_bodies(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/session":
                return httpx.Response(200, json={"id": "ses_123"})
            return httpx.Response(
                200,
                json={"info": {}, "parts": [{"type": "text", "text": "Sorted."}]},
            )

        client = OpenCodeClient(
            base_url="http://opencode.test",
            model=ModelRef("prov", "model-x"),
        )
        client._http = httpx.Client(
            base_url="http://opencode.test", transport=httpx.MockTransport(handler)
        )

        session_id = client.create_session(title="Casey C1 1.0")
        reply = client.prompt(session_id, text="VPN is down", system="You are Casey")

        assert session_id == "ses_123"
        assert reply == "Sorted."
        assert requests[0].url.path == "/session"
        assert json.loads(requests[0].content) == {"title": "Casey C1 1.0"}
        assert requests[1].url.path == "/session/ses_123/message"
        assert json.loads(requests[1].content) == {
            "model": {"providerID": "prov", "modelID": "model-x"},
            "system": "You are Casey",
            "tools": {"*": False},
            "parts": [{"type": "text", "text": "VPN is down"}],
        }


class TestRunCasey:
    def setup_method(self):
        self.fake_opencode = Mock(OpenCodeClient)
        self.fake_opencode.create_session.return_value = "ses_new"
        self.fake_opencode.prompt.return_value = "Try turning it off and on."
        support_agent._client = self.fake_opencode
        self.deps = CaseyDeps(
            client=Mock(WebClient),
            user_id="U1",
            channel_id="C1",
            thread_ts="1.0",
            message_ts="1.0",
        )

    def teardown_method(self):
        support_agent._client = None

    def test_creates_session_when_no_history(self):
        result = run_casey("Wifi is slow", self.deps, history=None)

        self.fake_opencode.create_session.assert_called_once_with(title="Casey C1 1.0")
        self.fake_opencode.prompt.assert_called_once_with(
            "ses_new", text="Wifi is slow", system=CASEY_SYSTEM_PROMPT, model=None
        )
        assert result.final_output == "Try turning it off and on."
        assert result.to_input_list() == [{"session_id": "ses_new"}]

    def test_reuses_session_from_history(self):
        result = run_casey(
            "Still slow", self.deps, history=[{"session_id": "ses_existing"}]
        )

        self.fake_opencode.create_session.assert_not_called()
        self.fake_opencode.prompt.assert_called_once_with(
            "ses_existing",
            text="Still slow",
            system=CASEY_SYSTEM_PROMPT,
            model=None,
        )
        assert result.session_id == "ses_existing"


class TestGlobalEventStream:
    def test_unwraps_envelope_and_attaches_directory(self, monkeypatch):
        sse = (
            b'data: {"payload": {"id": "evt_1", "type": "server.connected", "properties": {}}}\n\n'
            b'data: {"directory": "/w", "project": "global", "payload": {"id": "evt_2", '
            b'"type": "permission.asked", "properties": {"id": "per_1", "sessionID": "ses_1"}}}\n\n'
            b'data: {"directory": "/w", "payload": {"type": "sync", "syncEvent": {}}}\n\n'
            b"data: not json\n\n"
        )
        requested = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(request.url.path)
            return httpx.Response(
                200, content=sse, headers={"content-type": "text/event-stream"}
            )

        real_client = httpx.Client

        def fake_client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "Client", fake_client)
        client = OpenCodeClient(
            base_url="http://opencode.test", model=ModelRef("p", "m")
        )

        events = list(client.iter_events())

        assert requested == ["/global/event"]
        assert [e["type"] for e in events] == [
            "server.connected",
            "permission.asked",
            "sync",
        ]
        assert events[1]["directory"] == "/w"
        assert events[1]["properties"]["sessionID"] == "ses_1"
