# Phase 7 — WhatsApp channel (preparation)

Status: **boundary only, no live provider.** This is a decision record — read
`CLAUDE.md` §4 ("WhatsApp / channel interface"), §25 Phase 7, and `docs/phase-6.md`
first.

## What this step delivers

1. **`channels/whatsapp/`** — a provider-neutral translation boundary:
   * `models.py` — `InboundWhatsAppMessage` (`TEXT` / `INTERACTIVE_REPLY` /
     `UNSUPPORTED`) and `OutboundWhatsAppMessage` (`TEXT` / `DEFERRED_NOTICE`).
   * `mapping.py` — pure functions: `parse_inbound(dict)`,
     `to_message_request(...)`, `to_whatsapp_messages(AdvisoryReply)`,
     `channel_session_id(sender)`.
   * `gateway.py` — `WhatsAppGateway.handle_webhook(dict) -> tuple[Outbound…]`:
     parse → derive session id → get-or-`start_session` → `send_message` → map
     the reply.
   * `fake_transport.py` — `FakeWhatsAppTransport`, a record-only stand-in for a
     real provider send. **No Meta / Twilio / SDK dependency was added.**
2. **A Gemini first-response diagnostic** (`llm/diagnostics.py`) — sanitized,
   diagnostic-only. No token-limit or model change.

Nothing under `discovery/`, `market/`, `finance/`, `knowledge/`,
`conversation/`, or DPR was touched. `tests/test_channels_purity.py` AST-checks
that no `channels/` module imports an engine, the conversation state machine,
`llm/`, a data source, a geocoder, the database, or `httpx` — the boundary
calls `app/service.py` and uses `app/dto.py`, nothing else.

## The neutral inbound contract

`parse_inbound` does **not** model Meta Cloud's
`entry[].changes[].value.messages[]` nesting or Twilio's form-encoded body. A
provider-specific normaliser (added when a provider is chosen) is responsible
for producing this small dict:

```
{ "from": "<sender id, required>",
  "type": "text" | "interactive" | "<anything else>",
  "text": "<message>",                       # required when type == "text"
  "interactive": { "reply_id": "<id>", "title": "<label>" },  # type == "interactive"
  "message_id": "<provider id, optional>", "locale": "<optional>" }
```

`MalformedWebhookError` is raised for: not an object, no non-empty `from`, no
non-empty `type`, a `text` message with blank text, an `interactive` message
with no `interactive.reply_id`. A transport maps that to an HTTP 4xx and sends
nothing.

Any other `type` (audio, voice, image, document, location, …) parses to
`InboundKind.UNSUPPORTED` — a first-class deferred path, never an error and
never a fake transcription (CLAUDE.md §2: voice is deferred). The gateway
answers it with a `DEFERRED_NOTICE` bubble and does **not** start a session or
call `AdvisoryService`.

## Session identity

`channel_session_id(sender) == f"whatsapp:{sender.strip()}"` is used verbatim as
the `SessionRepository` key. Consequences, all deliberate:

* The sender's phone number is only ever an **identifier** here — it never
  enters a `MessageRequest` field that changes advice (CLAUDE.md §23, §3.1).
* The same number on `web` vs `whatsapp` is two isolated sessions
  (`tests/test_channels_whatsapp_gateway.py::test_two_senders_get_isolated_sessions`
  and the channel prefix).
* No separate phone→uuid map is persisted — the `SessionRepository` (in-memory
  or SQLite, unchanged) is the single source of truth. The gateway does
  `get_session` then `start_session(session_id=…)` only when absent, and retries
  once on a `SessionNotFoundError` race rather than dropping the message.

## Choice handling — text first

The Phase 6 deterministic renderer already emits numbered options as their own
lines (`"1. …"`, `"2. …"`). `to_whatsapp_messages` turns each `OutboundMessage`
into one text bubble and, when `reply.expects is CHOICE`, appends a plain
`"Reply with a number from 1 to N."` bubble — so a **text-only** provider is
sufficient; no interactive-list rendering is required.

Inbound, a choice is recognised two ways: an `interactive` reply carrying the
row id we set (`reply_id` = the 1-based index string) → `selected_choice`; or a
bare `^\s*\d{1,3}\s*$` text reply → `selected_choice`. The 1–3 digit bound keeps
a typed amount like `40000` from being read as a choice. A selection with no
pending choice is a no-op turn in the planner (verified), not an error.

## The Gemini diagnostic (Part B)

The one live conversational smoke test in the previous step needed a repair
round-trip even with `responseMimeType=application/json` set. This step adds a
**diagnostic-only** explanation, changing no behaviour:

* `llm/diagnostics.py::diagnose_gemini_response(payload, http_status)` →
  `GeminiResponseDiagnostic`: `finishReason`, `usageMetadata` token counts
  (including `thoughtsTokenCount`), `promptFeedback.blockReason`, a
  control-char-stripped / length-capped excerpt of the returned text, and a
  plain-language `note` (e.g. *"hit the output-token cap before emitting any
  text; N thinking tokens consumed the budget"* where N depends on the configured
  `llm_max_output_tokens`).
* `llm/gemini_provider.py` — **conversation role only**: on a 200 with no usable
  text part, log `summarize(diag)` at WARNING and raise `LlmPayloadError`
  carrying it. Still caught by `llm/structured.py`'s one repair attempt exactly
  as before. An optional `response_sink` hands the diagnostic to
  `scripts/phase6_gemini_diag.py` on every response (never set in production).
  The extractor / verifier code path is byte-identical.
* `llm/structured.py` — the existing "unparseable" WARNING now also logs a
  redacted excerpt of the raw response (covers the "prose, not JSON" mode).
* `scripts/phase6_gemini_diag.py` — makes **exactly one** live call and prints
  the sanitized diagnostic. **Not run** in this step (needs approval).

No token-limit bump, no model change — those remain open decisions.

## What is needed before connecting a real WhatsApp provider

1. **Pick a provider** — Meta WhatsApp Cloud API (direct) or an aggregator
   (Twilio, Gupshup, …). This decides the webhook shape the normaliser targets
   and the send API.
2. **Credentials / config** (added to `Settings` + `.env.example` then, none
   exist yet): a webhook **verify token**, the app **secret** for inbound
   signature validation, an outbound **access token**, the business
   **phone-number id**, and the provider **send base URL** (credential in a
   header, never the URL — mirrors `Settings._no_credential_in_url`).
3. **A transport** implementing `FakeWhatsAppTransport.send`'s signature with a
   real `httpx` POST (its own adapter module, isolated like `sources/osm/`).
4. **A webhook receiver** (Phase 7 proper, or a thin FastAPI/Flask shell) that
   verifies the signature, normalises the provider payload into the neutral
   dict, calls `WhatsAppGateway.handle_webhook`, and hands the bubbles to the
   transport.
5. **A conversational-LLM decision** — the diagnostic above should inform
   whether to raise `VYAPAR_LLM_MAX_OUTPUT_TOKENS` or switch model before real
   users (currently a repair round-trip adds ~5 s/turn).
6. **Privacy review** of storing `whatsapp:<phone>` as the session key vs a
   salted hash (CLAUDE.md §24 least-privilege).
