"""SQLAlchemy-backed `SessionRepository` (CLAUDE.md §25 Phase 6; mirrors
`database/sql.py::SqlBusinessRepository`) — the durable option the approved
plan calls for ("Protocol + in-memory + SQLite now") so a conversation
survives a process restart, which Phase 7's webhook (one process, callbacks
over days/weeks) will need.
"""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session

from vyaparsarathi.config import get_settings
from vyaparsarathi.conversation.session_models import ConversationSession
from vyaparsarathi.database.session_schema import SessionBase, SessionRow
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


class SqlSessionRepository:
    def __init__(self, engine: Engine, create_schema: bool = True) -> None:
        self._engine = engine
        if create_schema:
            SessionBase.metadata.create_all(engine)

    def get(self, session_id: str) -> ConversationSession | None:
        with Session(self._engine) as db:
            row = db.get(SessionRow, session_id)
            if row is None:
                return None
            return ConversationSession.model_validate(row.data)

    def save(self, session: ConversationSession) -> None:
        payload = session.model_dump(mode="json")
        with Session(self._engine) as db, db.begin():
            row = db.get(SessionRow, session.session_id)
            if row is None:
                row = SessionRow(session_id=session.session_id)
                db.add(row)
            row.turn_index = session.turn_index
            row.updated_at = (
                session.updated_at.astimezone(UTC)
                if session.updated_at.tzinfo
                else session.updated_at
            )
            row.data = payload

    def delete(self, session_id: str) -> None:
        with Session(self._engine) as db, db.begin():
            db.execute(delete(SessionRow).where(SessionRow.session_id == session_id))

    def list_ids(self) -> list[str]:
        with Session(self._engine) as db:
            return sorted(db.scalars(select(SessionRow.session_id)).all())


def create_session_repository(
    db_url: str | None = None, create_schema: bool = True
) -> SqlSessionRepository:
    url = db_url or get_settings().db_url
    engine = create_engine(url, future=True)
    logger.info(
        "using SQL session repository at %s", engine.url.render_as_string(hide_password=True)
    )
    return SqlSessionRepository(engine, create_schema=create_schema)


__all__ = ["SqlSessionRepository", "create_session_repository"]
