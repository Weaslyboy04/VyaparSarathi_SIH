"""`database/session_repository.py` — in-memory + SQLite implementations
(CLAUDE.md §25 Phase 6; mirrors `tests/test_repository.py`'s two-impl
parametrisation)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    Slot,
    SlotName,
    SlotState,
    SlotValue,
)
from vyaparsarathi.database.session_memory import InMemorySessionRepository
from vyaparsarathi.database.session_sql import SqlSessionRepository


@pytest.fixture(params=["memory", "sql"])
def repo(request: pytest.FixtureRequest):  # noqa: ANN201
    if request.param == "memory":
        return InMemorySessionRepository()
    engine = create_engine("sqlite://", future=True)  # in-memory SQLite
    return SqlSessionRepository(engine, create_schema=True)


def _session(session_id: str = "s1") -> ConversationSession:
    now = datetime.now(UTC)
    return ConversationSession(
        session_id=session_id,
        created_at=now,
        updated_at=now,
        turn_index=2,
        slots={
            SlotName.LIQUID_CASH_INR: Slot().updated(
                SlotValue(
                    state=SlotState.USER_PROVIDED, value=650_000, source="profile", set_on_turn=1
                )
            )
        },
    )


def test_get_missing_session_returns_none(repo) -> None:  # noqa: ANN001
    assert repo.get("nope") is None


def test_save_then_get_round_trips(repo) -> None:  # noqa: ANN001
    session = _session()
    repo.save(session)
    restored = repo.get("s1")
    assert restored is not None
    assert restored.turn_index == 2
    assert restored.slot(SlotName.LIQUID_CASH_INR).value == 650_000


def test_save_overwrites_by_session_id(repo) -> None:  # noqa: ANN001
    repo.save(_session())
    updated = _session().model_copy(update={"turn_index": 5})
    repo.save(updated)
    restored = repo.get("s1")
    assert restored.turn_index == 5


def test_delete_removes_the_session(repo) -> None:  # noqa: ANN001
    repo.save(_session())
    repo.delete("s1")
    assert repo.get("s1") is None


def test_list_ids(repo) -> None:  # noqa: ANN001
    repo.save(_session("a"))
    repo.save(_session("b"))
    assert repo.list_ids() == ["a", "b"]


def test_in_memory_repository_deep_copies_on_write_and_read() -> None:
    repo = InMemorySessionRepository()
    session = _session()
    repo.save(session)
    # A caller mutating its own object after save must not affect the store.
    mutated = session.model_copy(update={"turn_index": 999})
    repo.save(session)  # re-save the original, unmutated object
    restored_a = repo.get("s1")
    restored_b = repo.get("s1")
    assert restored_a is not restored_b  # two reads never share the same object
    assert mutated.turn_index != restored_a.turn_index


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
