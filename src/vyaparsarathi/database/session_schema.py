"""SQLAlchemy 2.x ORM schema for session persistence (CLAUDE.md §25 Phase 6;
mirrors `database/schema.py`).

One row per session, storing the serialized `ConversationSession` as JSON —
deliberately not decomposed into columns (unlike `businesses`/
`source_records`): the conversation model is Phase 6's own evolving shape,
not a stable cross-phase contract queried by other components, so a schema
migration on every new `SlotName` would be pure overhead. `updated_at` is a
plain indexed column so a future admin/ops query ("sessions idle > 30 days")
does not need to deserialize every row's JSON to filter.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SessionBase(DeclarativeBase):
    pass


class SessionRow(SessionBase):
    __tablename__ = "conversation_sessions"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON)

    __table_args__ = (Index("ix_conversation_sessions_updated_at", "updated_at"),)


__all__ = ["SessionBase", "SessionRow"]
