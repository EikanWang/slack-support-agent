import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:4096"
REQUEST_TIMEOUT_SECONDS = 180.0
CONNECT_RETRIES = 6
CONNECT_RETRY_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class ModelRef:
    provider_id: str
    model_id: str

    @classmethod
    def parse(cls, value: str) -> "ModelRef":
        provider_id, separator, model_id = value.partition("/")
        if not separator or not provider_id or not model_id:
            raise ValueError(
                f"OPENCODE_MODEL must look like '<provider>/<model>', got {value!r}"
            )
        return cls(provider_id=provider_id, model_id=model_id)

    def __str__(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


@dataclass(frozen=True)
class PermissionRule:
    permission: str
    pattern: str
    action: str

    def to_payload(self) -> dict:
        return {
            "permission": self.permission,
            "pattern": self.pattern,
            "action": self.action,
        }


class OpenCodeClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: ModelRef | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ):
        self._base_url = (
            base_url or os.environ.get("OPENCODE_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._model = model or ModelRef.parse(_require_env("OPENCODE_MODEL"))
        self._http = httpx.Client(base_url=self._base_url, timeout=timeout)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> ModelRef:
        return self._model

    def create_session(
        self,
        title: str,
        directory: str | None = None,
        permission: list[PermissionRule] | None = None,
    ) -> str:
        body: dict = {"title": title}
        if permission is not None:
            body["permission"] = [rule.to_payload() for rule in permission]
        response = self._post(
            "/session", json=body, params=_directory_params(directory)
        )
        return response.json()["id"]

    def prompt(
        self, session_id: str, text: str, system: str, model: ModelRef | None = None
    ) -> str:
        message = self.prompt_message(
            session_id, text=text, system=system, tools={"*": False}, model=model
        )
        return extract_text(message)

    def prompt_message(
        self,
        session_id: str,
        text: str,
        system: str,
        tools: dict[str, bool] | None = None,
        directory: str | None = None,
        timeout: float | None = None,
        model: ModelRef | None = None,
    ) -> dict:
        chosen = model or self._model
        body: dict = {
            "model": {
                "providerID": chosen.provider_id,
                "modelID": chosen.model_id,
            },
            "system": system,
            "parts": [{"type": "text", "text": text}],
        }
        if tools is not None:
            body["tools"] = tools
        try:
            response = self._post(
                f"/session/{session_id}/message",
                json=body,
                params=_directory_params(directory),
                timeout=timeout,
            )
        except OpenCodeRequestError as error:
            if error.status_code >= 500:
                self._check_model_available(chosen)
            raise
        return response.json()

    def reply_permission(
        self,
        session_id: str,
        permission_id: str,
        response: str,
        directory: str | None = None,
    ) -> None:
        self._post(
            f"/session/{session_id}/permissions/{permission_id}",
            json={"response": response},
            params=_directory_params(directory),
        )

    def abort_session(self, session_id: str, directory: str | None = None) -> None:
        self._post(
            f"/session/{session_id}/abort",
            json={},
            params=_directory_params(directory),
        )

    def available_models(self) -> list[ModelRef]:
        """Every model the OpenCode server has a configured provider for."""
        response = self._http.get("/config/providers")
        response.raise_for_status()
        payload = response.json()
        return [
            ModelRef(provider_id=provider["id"], model_id=model_id)
            for provider in payload.get("providers", [])
            for model_id in sorted(provider.get("models", {}))
        ]

    def iter_events(self) -> Iterator[dict]:
        """Yield events for every project directory the server knows about.

        Each item is the inner event (``type``/``properties``) with the owning
        ``directory`` added, so callers can route per-session.
        """
        with (
            httpx.Client(base_url=self._base_url, timeout=None) as stream_client,
            stream_client.stream("GET", "/global/event") as response,
        ):
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    envelope = json.loads(line[len("data:") :])
                except json.JSONDecodeError:
                    continue
                event = envelope.get("payload")
                if not isinstance(event, dict) or "type" not in event:
                    continue
                yield {**event, "directory": envelope.get("directory")}

    def close(self) -> None:
        self._http.close()

    def _post(
        self,
        path: str,
        json: dict,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        request_timeout = timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
        for attempt in range(CONNECT_RETRIES):
            try:
                response = self._http.post(
                    path, json=json, params=params, timeout=request_timeout
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as error:
                raise OpenCodeRequestError.from_response(error.response) from error
            except httpx.ConnectError as error:
                last_error = error
                time.sleep(CONNECT_RETRY_DELAY_SECONDS * (attempt + 1))
        raise OpenCodeUnavailable(
            f"OpenCode server at {self._base_url} is not reachable "
            f"(is `scripts/dev.sh up` running?)"
        ) from last_error

    def _check_model_available(self, model: ModelRef) -> None:
        """Raise a pointed error when the server has no such model configured.

        OpenCode answers an unknown model with a bare 500 whose body says only
        "check server logs", so the catalogue is the only way to tell the user
        what is actually wrong.
        """
        try:
            available = self.available_models()
        except (httpx.HTTPError, OSError):
            return
        if model in available:
            return
        suggestions = [
            str(candidate)
            for candidate in available
            if candidate.model_id == model.model_id
        ][:3]
        hint = (
            f" Did you mean {', '.join(f'`{s}`' for s in suggestions)}?"
            if suggestions
            else ""
        )
        raise OpenCodeModelError(
            f"OpenCode has no model `{model}` configured "
            f"({len(available)} are available).{hint}"
        )


def _directory_params(directory: str | None) -> dict | None:
    return {"directory": directory} if directory else None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.sample to .env and fill it in "
            f"(see README: Configuration)."
        )
    return value


class OpenCodeUnavailable(RuntimeError):
    pass


class OpenCodeModelError(RuntimeError):
    pass


class OpenCodeRequestError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail

    @classmethod
    def from_response(cls, response: httpx.Response) -> "OpenCodeRequestError":
        detail = _error_detail(response)
        return cls(
            response.status_code,
            f"OpenCode returned HTTP {response.status_code} for "
            f"{response.request.url.path}: {detail}",
        )


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300].strip() or "no response body"
    if not isinstance(payload, dict):
        return str(payload)[:300]
    data = payload.get("data")
    parts = [str(payload.get("name") or "")]
    if isinstance(data, dict):
        parts.append(str(data.get("message") or ""))
        if data.get("ref"):
            parts.append(f"(ref {data['ref']}; see `opencode` server logs)")
    return " ".join(part for part in parts if part) or response.text[:300]


class OpenCodeCredentialError(RuntimeError):
    pass


CREDENTIAL_ERROR_MARKERS = (
    "expired",
    "invalid token",
    "unauthorized",
    "401",
    "403",
    "security token",
    "bearer token",
    "authentication",
)


def raise_for_message_error(message: dict) -> None:
    error = message.get("info", {}).get("error")
    if not error:
        return
    detail = error.get("data", {}).get("message") or error.get("name", "unknown")
    if any(marker in detail.lower() for marker in CREDENTIAL_ERROR_MARKERS):
        raise OpenCodeCredentialError(
            "OpenCode's model credentials were rejected (likely the weekly "
            f"token expired). Refresh the credentials file; detail: {detail}"
        )
    raise RuntimeError(f"OpenCode returned an error: {detail}")


def extract_text(message: dict) -> str:
    raise_for_message_error(message)
    chunks = [
        part["text"]
        for part in message.get("parts", [])
        if part.get("type") == "text" and part.get("text")
    ]
    return "\n".join(chunks).strip()
