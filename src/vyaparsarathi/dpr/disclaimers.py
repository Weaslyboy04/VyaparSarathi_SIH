"""Fixed, human-authored wording for the DPR (CLAUDE.md §1 "What VyaparSarathi
is NOT", §3.5, §22, §30). PURE — constants only.

None of this is generated or rewritten by an LLM. The disclaimer and the
limitation notes are deliberately plain and are printed verbatim on every
report.
"""

from __future__ import annotations

REPORT_KIND = "Structured decision-support report (bank / official hand-off)"

DISCLAIMER = (
    "This is decision-support material prepared before capital is committed. It is NOT a "
    "loan sanction, an approval, a guarantee of viability, or financial advice of record. "
    "Every figure is either stated by the entrepreneur (and unverified), retrieved from a "
    "cited official document, produced by a deterministic calculation from named inputs, "
    "or an explicitly labelled assumption. Where an input or a piece of evidence is "
    "absent, the report says so rather than substituting a default. A bank or scheme "
    "authority's own appraisal and the official scheme rules remain authoritative."
)

MARKET_COMPLETENESS_NOTE = (
    "The nearby-business data is a partial, sourced sample (primarily OpenStreetMap, "
    "whose rural coverage is uneven), never the complete set of businesses in the area. "
    "Absence of a competitor in the data does not prove the business does not exist. "
    "Read every competitor count together with the source-coverage confidence beside it."
)

CONFIDENCE_SEPARATION_NOTE = (
    "Market-data confidence measures only how well the local market could be OBSERVED "
    "here. It is a separate measurement from financial feasibility: a plan can be "
    "financially sound with low market-data confidence, or financially weak with high "
    "confidence. Neither figure is multiplied into the other."
)

DECLARED_CONFIG_NOTE = (
    "The promoter-margin percentage and loan-to-cost ratio shown under 'Financing "
    "structure' are the SIH26091 problem statement's own declared financing structure — "
    "configuration this deployment states, NOT a rule retrieved from a scheme document. "
    "They are never presented as an official scheme fact."
)

FINANCIAL_INCOMPLETE_NOTE = (
    "A full financial verdict (DSCR, monthly cash flow, break-even and stress scenarios) "
    "could not be produced because one or more core drivers were not stated. The exact "
    "missing inputs are listed below. Nothing has been assumed in their place."
)

PROFILE_UNVERIFIED_NOTE = (
    "Everything in this section was stated by the entrepreneur and has not been "
    "independently verified. It is carried through the analysis as declared."
)

PROJECT_PLAN_NOTE = (
    "Only business and project information the entrepreneur actually stated is shown. No "
    "technical specification, layout, machinery list, or manpower plan is inferred — that "
    "detail is gathered separately during scheme appraisal."
)

DISTRIBUTION_CHANNELS_NOTE = (
    "This is generic guidance for businesses of this category — never a specific supplier, "
    "buyer, or channel discovered at this location. Verify locally before relying on it."
)

WHOLESALE_NOT_RETAIL_NOTE = (
    "These are wholesale (mandi) prices, in Rupees per quintal — what farmers/traders "
    "received, not a suggested retail selling price for a shop. Retail prices, margins, "
    "and local competition are separate decisions this figure does not make for you."
)

NO_LLM_NOTE = (
    "This report was assembled and rendered deterministically from stored structured "
    "results. No language model drafted, summarised, or rewrote any section."
)

GLOSSARY: tuple[tuple[str, str], ...] = (
    (
        "Available Margin Capital",
        "The liquid cash the entrepreneur can put into the project. Physical assets are "
        "not counted here (CLAUDE.md §13).",
    ),
    (
        "Promoter margin / contribution",
        "The share of the project cost the borrower must fund themselves. A scheme states "
        "its own required percentage; whether an asset counts toward it is a scheme rule.",
    ),
    (
        "EMI",
        "Equated Monthly Instalment — the fixed monthly loan repayment after any "
        "moratorium, covering interest and principal on a reducing balance.",
    ),
    (
        "Moratorium",
        "An initial period during which principal repayment (and sometimes interest) is "
        "deferred. Operating costs still run during it.",
    ),
    (
        "DSCR",
        "Debt-Service Coverage Ratio = cash available for debt service ÷ debt due in the "
        "same period. Above ~1.25 is generally considered comfortable. Computed on a cash "
        "basis here (no depreciation add-back, no tax).",
    ),
    (
        "Break-even",
        "The monthly revenue, or the month, at which the business first covers its costs "
        "(operating break-even) or its costs plus loan servicing (break-even incl. debt).",
    ),
    (
        "Working capital",
        "Cash tied up in day-to-day operations — inventory and receivables, net of "
        "supplier credit — plus a cushion for the ramp-up months.",
    ),
    (
        "Catchment",
        "The area a business can realistically draw customers from. Here it is a "
        "fixed-radius approximation; travel-time catchments are a later enhancement.",
    ),
    (
        "Market-data confidence",
        "How completely the local market could be observed from available sources. Not a "
        "probability of success.",
    ),
    (
        "Opportunity score",
        "A 0–100 comparison of candidate businesses for this location and profile. A "
        "ranking aid, not a prediction of profit.",
    ),
)


__all__ = [
    "CONFIDENCE_SEPARATION_NOTE",
    "DECLARED_CONFIG_NOTE",
    "DISCLAIMER",
    "DISTRIBUTION_CHANNELS_NOTE",
    "FINANCIAL_INCOMPLETE_NOTE",
    "GLOSSARY",
    "MARKET_COMPLETENESS_NOTE",
    "NO_LLM_NOTE",
    "WHOLESALE_NOT_RETAIL_NOTE",
    "PROFILE_UNVERIFIED_NOTE",
    "PROJECT_PLAN_NOTE",
    "REPORT_KIND",
]
