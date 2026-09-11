"""Turn a gap into a question (CLAUDE.md §2, §25 Phase 6). PURE.

Two kinds of gap drive a question: a hard structural blocker (no location, no
proposed business — `SLOT_QUESTIONS`) that the planner asks about before any
engine runs at all, and a financial driver
`finance/assessment.py::missing_core_drivers` names after `ASSESS_FINANCE`
has already run and returned `INSUFFICIENT_FINANCIAL_EVIDENCE` — these are
folded into the delivered narrative rather than blocking a turn (CLAUDE.md
§25's "DELIVER_PARTIAL matters: the user sees the market half while finance
is still blocked", generalised: the whole DAG runs as far as it structurally
can, and financial gaps become itemised follow-ups a later turn answers).

``MISSING_DRIVER_QUESTIONS``' keys are asserted, in
`tests/test_conversation_clarify.py`, to equal exactly the four literal
strings `_missing_core_drivers` can emit — a Phase 4 wording change then
fails this suite loudly instead of silently falling back to raw text.
"""

from __future__ import annotations

from vyaparsarathi.conversation.readiness import CollectionItem
from vyaparsarathi.conversation.session_models import SlotName

SLOT_QUESTIONS: dict[SlotName, str] = {
    SlotName.LOCATION_TEXT: (
        "Where are you planning to start this business? A village/town name and state "
        "is enough (e.g. 'Bhagwanpur, Bihar')."
    ),
    SlotName.PROPOSED_BUSINESS_TEXT: "What kind of business are you thinking of starting?",
}

# NORMAL mode's rung 6 (`planner.py`) asks these, in `readiness.COLLECTION_ORDER`,
# before it will show a consolidated advisory. The business/location wording
# is reused verbatim from `SLOT_QUESTIONS` rather than duplicated.
ITEM_QUESTIONS: dict[CollectionItem, str] = {
    CollectionItem.PROPOSED_BUSINESS_TEXT: SLOT_QUESTIONS[SlotName.PROPOSED_BUSINESS_TEXT],
    CollectionItem.LOCATION_TEXT: SLOT_QUESTIONS[SlotName.LOCATION_TEXT],
    CollectionItem.OWNED_ASSETS: (
        "Do you already have a shop, land, equipment, a vehicle, or other business "
        "assets? Say what you have, or 'none'."
    ),
    CollectionItem.TRADE_EXPERIENCE: (
        "Do you have experience running or working in this trade, or a related one? "
        "Name it, or say 'none'."
    ),
    CollectionItem.YEARS_EXPERIENCE: (
        "How many years of experience do you have in this trade or a related one? "
        "Say 'zero' if you're starting fresh."
    ),
    CollectionItem.LIQUID_CASH_INR: (
        "How much cash can you put toward this — inventory, working capital, setting "
        "up? This is your available margin capital."
    ),
}

MISSING_DRIVER_QUESTIONS: dict[str, str] = {
    "a revenue driver (monthly_revenue, or unit_price + units_per_month)": (
        "About how much do you expect to sell in a typical month, in rupees?"
    ),
    "a margin driver (cogs_pct or gross_margin_pct)": (
        "Roughly what share of your revenue goes toward buying stock (cost of goods), "
        "as a percentage?"
    ),
    "at least one project-cost line": (
        "About how much will it cost to set up — shop fit-out, equipment, first stock — in total?"
    ),
    "fixed operating-expense lines (state a Rs 0 line if there genuinely are none)": (
        "What are your monthly fixed costs — rent, electricity, wages? (say 'Rs 0' if "
        "there genuinely are none)"
    ),
}


def question_for_slot(name: SlotName) -> str:
    return SLOT_QUESTIONS.get(name, f"Could you tell me about {name.value.replace('_', ' ')}?")


def question_for_item(item: CollectionItem) -> str:
    return ITEM_QUESTIONS.get(item, f"Could you tell me about {item.value.replace('_', ' ')}?")


def question_for_missing_driver(driver_text: str) -> str:
    return MISSING_DRIVER_QUESTIONS.get(driver_text, f"I still need: {driver_text}")


__all__ = [
    "ITEM_QUESTIONS",
    "MISSING_DRIVER_QUESTIONS",
    "SLOT_QUESTIONS",
    "question_for_item",
    "question_for_missing_driver",
    "question_for_slot",
]
