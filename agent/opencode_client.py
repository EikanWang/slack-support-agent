import os
import time
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

    def create_session(self, title: str) -> str:
        response = self._post("/session", json={"title": title})
        return response.json()["id"]

    def prompt(self, session_id: str, text: str, system: str) -> str:
        body = {
            "model": {
                "providerID": self._model.provider_id,
                "modelID": self._model.model_id,
            },
            "system": system,
            "tools": {"*": False},
            "parts": [{"type": "text", "text": text}],
        }
        response = self._post(f"/session/{session_id}/message", json=body)
        return _extract_text(response.json())

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, json: dict) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(CONNECT_RETRIES):
            try:
                response = self._http.post(path, json=json)
                response.raise_for_status()
                return response
            except httpx.ConnectError as error:
                last_error = error
                time.sleep(CONNECT_RETRY_DELAY_SECONDS * (attempt + 1))
        raise OpenCodeUnavailable(
            f"OpenCode server at {self._base_url} is not reachable "
            f"(is `scripts/dev.sh up` running?)"
        ) from last_error


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


def _extract_text(message: dict) -> str:
    error = message.get("info", {}).get("error")
    if error:
        detail = error.get("data", {}).get("message") or error.get("name", "unknown")
        if any(marker in detail.lower() for marker in CREDENTIAL_ERROR_MARKERS):
            raise OpenCodeCredentialError(
                "OpenCode's model credentials were rejected (likely the weekly "
                f"token expired). Refresh the credentials file; detail: {detail}"
            )
        raise RuntimeError(f"OpenCode returned an error: {detail}")

    chunks = [
        part["text"]
        for part in message.get("parts", [])
        if part.get("type") == "text" and part.get("text")
    ]
    return "\n".join(chunks).strip()
