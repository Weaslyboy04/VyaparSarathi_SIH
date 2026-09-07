"""The LLM provider boundary (CLAUDE.md §4.1, §24, §25 Phase 6).

`HttpLlmProvider` mirrors `sources/osm/client.py::OverpassClient` exactly —
borrow-or-own the `httpx.Client`, an injectable `sleep`, a context manager —
and calls the same `utils/http.py::request_with_retry`, which already
retries `{429, 500, 502, 503, 504}` and honours `Retry-After`: precisely an
LLM endpoint's failure modes, so this needed zero new retry logic.

The wire contract is deliberately minimal and provider-agnostic (CLAUDE.md
§4.1: "an LLM client library ... chosen in Phases 6-7, not before" — none
is): `POST {settings.llm_base_url}` with
``{"model", "messages": [{"role","content"}], "max_tokens", "temperature"}``,
expecting back ``{"text": "..."}``. A real deployment points
`VYAPAR_LLM_BASE_URL` at a small compatibility shim in front of whatever
vendor API is actually used; no vendor SDK is imported here or anywhere in
this repository.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import httpx

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.errors import HttpError, LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.llm_models import LlmRequest, LlmResponse
from vyaparsarathi.utils.http import request_with_retry
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


class LlmProvider(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse: ...


class HttpLlmProvider:
    """The only module in this repository allowed to call
    `Settings.llm_api_key.get_secret_value()` (`tests/test_llm_secrets.py`
    AST-checks this). The key is read once, in `__init__`, into a header —
    never re-read per request, never placed in the URL (CLAUDE.md §24;
    `config/settings.py::Settings._no_credential_in_url`)."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        headers = {"User-Agent": self._settings.user_agent, "Content-Type": "application/json"}
        if self._settings.llm_api_key is not None:
            headers["Authorization"] = f"Bearer {self._settings.llm_api_key.get_secret_value()}"
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.Client(
                timeout=self._settings.llm_timeout_s, headers=headers, follow_redirects=True
            )
            self._owns_client = True
        self._headers = headers
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpLlmProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def complete(self, request: LlmRequest) -> LlmResponse:
        if not self._settings.llm_base_url:
            raise LlmUnavailableError("VYAPAR_LLM_BASE_URL is not configured")

        body = {
            "model": self._settings.llm_model,
            "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        retry_kwargs: dict[str, Any] = {
            "max_retries": self._settings.llm_max_retries,
            "backoff_base_s": self._settings.llm_backoff_base_s,
        }
        if self._sleep is not None:
            retry_kwargs["sleep"] = self._sleep

        try:
            response = request_with_retry(
                self._client,
                "POST",
                self._settings.llm_base_url,
                json=body,
                headers=self._headers,
                **retry_kwargs,
            )
        except HttpError as exc:
            raise LlmUnavailableError(f"LLM provider unavailable: {exc}") from exc

        if response.status_code >= 400:
            raise LlmUnavailableError(f"LLM provider returned HTTP {response.status_code}")

        try:
            payload = response.json()
            text = payload["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LlmPayloadError(f"LLM provider returned an unparseable response: {exc}") from exc
        if not isinstance(text, str):
            raise LlmPayloadError("LLM provider response 'text' field was not a string")

        return LlmResponse(text=text, prompt_id=request.prompt_id)


__all__ = ["HttpLlmProvider", "LlmProvider"]
