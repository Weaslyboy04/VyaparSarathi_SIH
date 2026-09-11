"""Turn a conversation `Slot` into a `ProvenancedValue` without losing its
state (CLAUDE.md §23). PURE.

The mapping is exact: a `USER_PROVIDED` slot becomes a user-provided value, an
`ASSUMED` slot an assumption (carrying its stored rationale), a `SOURCED` slot
a sourced value (carrying its citation ref), `CALCULATED` a calculation, and
`MISSING` / `DECLINED` / `AMBIGUOUS` become an explicit gap — never a blank
and never a substituted default.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from vyaparsarathi.conversation.session_models import ConversationSession, Slot, SlotName, SlotState
from vyaparsarathi.dpr.provenance import (
    GapReason,
    ProvenancedValue,
    pv_assumed,
    pv_calc,
    pv_config,
    pv_missing,
    pv_sourced,
    pv_user,
)

Formatter = Callable[[object], str]


def _raw(slot: Slot) -> str | None:
    v = slot.current.value
    return None if v is None else str(v)


def slot_value(
    session: ConversationSession,
    name: SlotName,
    *,
    label: str,
    fmt: Formatter,
    missing_reason: GapReason = GapReason.INPUT_REQUIRED,
    citation_id: str | None = None,
) -> ProvenancedValue:
    slot = session.slot(name)
    state = slot.state
    if state is SlotState.MISSING:
        return pv_missing(label, reason=missing_reason)
    if state is SlotState.DECLINED:
        return pv_missing(label, reason=GapReason.DECLINED)
    if state is SlotState.AMBIGUOUS:
        return pv_missing(
            label,
            reason=GapReason.AMBIGUOUS,
            note="; ".join(slot.current.options),
        )

    display = fmt(slot.current.value)
    raw = _raw(slot)
    if state is SlotState.USER_PROVIDED:
        note = f'stated as "{slot.current.raw_text}"' if slot.current.raw_text else ""
        return pv_user(label, display, raw=raw, note=note)
    if state is SlotState.ASSUMED:
        source = slot.current.source or ""
        rationale = slot.current.rationale or "A configured default was applied."
        maker = pv_config if source == "config:sih_scheme" else pv_assumed
        return maker(label, display, rationale=rationale, raw=raw)
    if state is SlotState.SOURCED:
        cid = citation_id or (slot.current.source_ref or slot.current.source or "source")
        return pv_sourced(label, display, citation_id=cid, raw=raw)
    # CALCULATED
    return pv_calc(
        label, display, inputs=tuple(slot.current.calculated_from) or ("engine",), raw=raw
    )


def as_int(value: object) -> int:
    if isinstance(value, Decimal | int):
        return int(value)
    return int(str(value))


def as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


__all__ = ["Formatter", "as_decimal", "as_int", "slot_value"]
