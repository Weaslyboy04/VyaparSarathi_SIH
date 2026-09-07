"""The channel-neutral application backend (CLAUDE.md §25 Phase 6, and the
user's explicit clarification: Phase 6 owns this layer).

`AdvisoryService` is the one entry point a channel (CLI now; WhatsApp in
Phase 7; a web UI later) ever calls. It owns session lifecycle and
persistence (`database/session_*`) and drives one turn of the deterministic
pipeline (`llm/orchestrator.py`) per inbound message — it never talks to a
transport (no HTTP server here) and never duplicates domain logic from
Phases 1-5.
"""

from __future__ import annotations
