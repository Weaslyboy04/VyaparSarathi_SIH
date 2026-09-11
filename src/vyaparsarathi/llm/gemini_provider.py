"""A Gemini-native `LlmProvider` (CLAUDE.md §4.1, §18, §30).

Gemini's REST API does not speak `HttpLlmProvider`'s generic wire contract
(`{"model","messages",...}` -> `{"text": ...}`): it wants
`{"contents": [...], "systemInstruction": {...}, "generationConfig": {...}}`
posted to a per-model URL, and returns a differently-nested
`{"candidates": [{"content": {"parts": [{"text": ...}]}}]}` shape. This module
translates between the two so `GeminiLlmProvider` is a structural drop-in for
`LlmProvider` (`llm/provider.py`) anywhere one is accepted:

* `role="extractor"` / `role="verifier"` — the offline Phase 5 dual-Gemini
  knowledge-extraction gate (`scripts/build_parameter_registry.py`), reading
  the independent `Settings.gemini_extractor_*` / `gemini_verifier_*` fields.
* `role="conversation"` — the Phase 6 conversational layer, reading the
  SEPARATE `Settings.llm_*` fields (`VYAPAR_LLM_API_KEY` / `VYAPAR_LLM_MODEL`
  / `VYAPAR_LLM_TIMEOUT_S` / ...). Used by `app/runtime.py` when
  `llm_enabled` is set but no generic `VYAPAR_LLM_BASE_URL` shim is
  configured, so a deployment that only has a Gemini key still gets
  free-text extraction. This key never touches the offline extraction gate
  and vice versa.

Still zero vendor SDK: this is a plain `httpx` POST, same as
`HttpLlmProvider`, reusing the identical `utils/http.py::request_with_retry`
helper (429/5xx/timeout retry, `Retry-After` honoured).

Auth is the `x-goog-api-key` HEADER, never a `?key=` query parameter —
Gemini supports both, but a credential in the URL would leak into
`request_with_retry`'s own WARNING-level logging and into `HttpError`'s
message on failure (the same reasoning `Settings._no_credential_in_url`
already enforces for the Phase 6 conversational provider).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.errors import HttpError, LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.diagnostics import (
    GeminiResponseDiagnostic,
    diagnose_gemini_response,
    dig_text,
    summarize,
)
from vyaparsarathi.llm.llm_models import LlmRequest, LlmResponse, LlmRole
from vyaparsarathi.utils.http import request_with_retry
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

Role = Literal["extractor", "verifier", "conversation"]


class GeminiLlmProvider:
    """`role` selects which credential/model/timeout block of `Settings` this
    instance speaks for — `gemini_extractor_*`, `gemini_verifier_*`, or the
    Phase 6 conversational `llm_*` — one instance per role, never shared. The
    only module in this repository (besides `llm/provider.py` itself) allowed
    to call `.get_secret_value()` (`tests/test_llm_secrets.py`'s AST check
    names both files)."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        role: Role,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
        response_sink: Callable[[GeminiResponseDiagnostic], None] | None = None,
    ) -> None:
        # `response_sink` is diagnostic-only: when set (by
        # `scripts/phase6_gemini_diag.py`), every conversation-role response
        # is described and handed over — usage metadata, finish reason,
        # redacted excerpt — before parsing. Never set in production.
        self._settings = settings or get_settings()
        self._role = role
        self._response_sink = response_sink
        s = self._settings
        api_key: SecretStr | None
        if role == "extractor":
            api_key = s.gemini_extractor_api_key
            self._model = s.gemini_extractor_model
            self._timeout_s = s.gemini_timeout_s
            self._max_retries = s.gemini_max_retries
            self._backoff_base_s = s.gemini_backoff_base_s
            self._key_env_var = "VYAPAR_GEMINI_EXTRACTOR_API_KEY"
        elif role == "verifier":
            api_key = s.gemini_verifier_api_key
            self._model = s.gemini_verifier_model
            self._timeout_s = s.gemini_timeout_s
            self._max_retries = s.gemini_max_retries
            self._backoff_base_s = s.gemini_backoff_base_s
            self._key_env_var = "VYAPAR_GEMINI_VERIFIER_API_KEY"
        else:  # "conversation" — the Phase 6 conversational config (llm_*),
            # a credential and model kept entirely separate from the offline
            # Phase 5 extraction gate above.
            api_key = s.llm_api_key
            self._model = s.llm_model
            self._timeout_s = s.llm_timeout_s
            self._max_retries = s.llm_max_retries
            self._backoff_base_s = s.llm_backoff_base_s
            self._key_env_var = "VYAPAR_LLM_API_KEY"

        headers = {"Content-Type": "application/json"}
        if api_key is not None:
            headers["x-goog-api-key"] = api_key.get_secret_value()
        self._api_key_present = api_key is not None

        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.Client(
                timeout=self._timeout_s, headers=headers, follow_redirects=True
            )
            self._owns_client = True
        self._headers = headers
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GeminiLlmProvider:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def complete(self, request: LlmRequest) -> LlmResponse:
        if not self._api_key_present:
            raise LlmUnavailableError(
                f"no Gemini API key configured for role={self._role!r} (set {self._key_env_var})"
            )
        if not self._model:
            raise LlmUnavailableError(f"no Gemini model configured for role={self._role!r}")

        system_parts = [m.content for m in request.messages if m.role is LlmRole.SYSTEM]
        contents = [
            {
                "role": "model" if m.role is LlmRole.ASSISTANT else "user",
                "parts": [{"text": m.content}],
            }
            for m in request.messages
            if m.role is not LlmRole.SYSTEM
        ]
        generation_config: dict[str, Any] = {
            "maxOutputTokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if self._role == "conversation":
            # Every conversational prompt (`llm/prompts.py`) asks for a single
            # JSON object; forcing the response MIME type makes
            # `llm/structured.py`'s parse succeed on the first try instead of
            # paying a repair round-trip. Deliberately NOT set for the
            # extractor/verifier roles — their prompt/response contract is
            # owned by `scripts/build_parameter_registry.py`.
            generation_config["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation_config}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        url = f"{_GEMINI_BASE_URL}/models/{self._model}:generateContent"
        retry_kwargs: dict[str, Any] = {
            "max_retries": self._max_retries,
            "backoff_base_s": self._backoff_base_s,
        }
        if self._sleep is not None:
            retry_kwargs["sleep"] = self._sleep

        try:
            response = request_with_retry(
                self._client, "POST", url, json=body, headers=self._headers, **retry_kwargs
            )
        except HttpError as exc:
            raise LlmUnavailableError(f"Gemini provider ({self._role}) unavailable: {exc}") from exc

        if response.status_code >= 400:
            raise LlmUnavailableError(
                f"Gemini provider ({self._role}) returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            payload = response.json()
            if self._role == "conversation":
                # Diagnostic-only branch. On a 200 that carries no usable text
                # (MAX_TOKENS with the budget spent on thinking tokens, a
                # safety block, an empty candidate) raise an error whose
                # message explains *why* — it still flows into
                # `llm/structured.py`'s one repair attempt exactly as before.
                conv_text = dig_text(payload)
                usable = isinstance(conv_text, str) and bool(conv_text)
                if self._response_sink is not None or not usable:
                    diag = diagnose_gemini_response(payload, http_status=response.status_code)
                    if self._response_sink is not None:
                        self._response_sink(diag)
                    if not usable:
                        logger.warning(
                            "gemini conversation: unusable response: %s", summarize(diag)
                        )
                        raise LlmPayloadError(
                            "Gemini provider (conversation) returned no usable text: "
                            f"{summarize(diag)}"
                        )
                assert isinstance(conv_text, str)
                return LlmResponse(text=conv_text, prompt_id=request.prompt_id)
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmPayloadError(
                f"Gemini provider ({self._role}) returned an unparseable response: {exc}"
            ) from exc
        if not isinstance(text, str):
            raise LlmPayloadError(f"Gemini provider ({self._role}) response text was not a string")

        return LlmResponse(text=text, prompt_id=request.prompt_id)


__all__ = ["GeminiLlmProvider", "Role"]
