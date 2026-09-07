"""The session-persistence contract (CLAUDE.md §25 Phase 6's "session/state
persistence is handled at the backend/application layer").

Mirrors `database/repository.py::BusinessRepository`: a `typing.Protocol`, so
`app/service.py` depends only on this shape and either implementation (or a
future one) can be swapped in without touching the backend or `conversation/`.
"""

from __future__ import annotations

from typing import Protocol

from vyaparsarathi.conversation.session_models import ConversationSession


class SessionRepository(Protocol):
    def get(self, session_id: str) -> ConversationSession | None: ...

    def save(self, session: ConversationSession) -> None: ...

    def delete(self, session_id: str) -> None: ...

    def list_ids(self) -> list[str]: ...


__all__ = ["SessionRepository"]
