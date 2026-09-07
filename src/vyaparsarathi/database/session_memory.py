"""In-memory `SessionRepository` — default for the CLI, the demo and unit
tests (mirrors `database/memory.py::InMemoryBusinessRepository`).

Deep-copies on every read and write (`ConversationSession.model_copy(deep=True)`)
so one caller's handle can never mutate another's stored state — CLAUDE.md
§24: "do not expose one user's data to another."
"""

from __future__ import annotations

from vyaparsarathi.conversation.session_models import ConversationSession


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, ConversationSession] = {}

    def get(self, session_id: str) -> ConversationSession | None:
        found = self._sessions.get(session_id)
        return found.model_copy(deep=True) if found is not None else None

    def save(self, session: ConversationSession) -> None:
        self._sessions[session.session_id] = session.model_copy(deep=True)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def list_ids(self) -> list[str]:
        return sorted(self._sessions)


__all__ = ["InMemorySessionRepository"]
