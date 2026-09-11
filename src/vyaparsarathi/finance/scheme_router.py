"""Deterministic scheme selection (CLAUDE.md §4's separate "scheme / credit
routing engine"; Tier 1 "Deterministic Scheme Router").

`docs/phase-5.md` is explicit that Phase 5's resolver is *not* a scheme
router — "it does not decide which scheme applies." This module is that
router: it selects among the declared `SihSchemeConfig` table
(`config/sih_scheme.py::DEFAULT_SIH_SCHEME_TABLE`) by project-cost band when
a project cost is known, or is a bare presence check when it is not yet
known (`finance/structuring.py`'s first, pre-cost gate; `finance/capacity.py`
band-searches the table itself instead of calling this with a cost, since it
is solving the inverse problem — see that module).

PURE. No I/O, no clock, no randomness — a router over declared configuration
is deterministic by construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.config.sih_scheme import SihSchemeConfig
from vyaparsarathi.models.taxonomy import BusinessCategory


class SchemeRoutingStatus(StrEnum):
    SELECTED = "selected"
    NOT_CONFIGURED = "not_configured"  # no scheme is declared for this deployment


class SchemeRoutingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SchemeRoutingStatus
    selected: SihSchemeConfig | None = None
    reasons: list[str] = Field(default_factory=list)


def normalize_schemes(
    schemes: SihSchemeConfig | Sequence[SihSchemeConfig] | None,
) -> tuple[SihSchemeConfig, ...]:
    """A single declared config, a table of them, or `None` — all callers
    (`structure_financing`, `compute_scheme_capacity`) accept any of the
    three; this is the one place that normalizes to a tuple."""
    if schemes is None:
        return ()
    if isinstance(schemes, SihSchemeConfig):
        return (schemes,)
    return tuple(schemes)


def scheme_covers_project_cost(cfg: SihSchemeConfig, project_cost_inr: Decimal) -> bool:
    """Whether `cfg`'s declared `[min_project_cost_inr, max_project_cost_inr]`
    band contains `project_cost_inr` — an unset bound is open on that side."""
    if cfg.min_project_cost_inr is not None and project_cost_inr < cfg.min_project_cost_inr:
        return False
    if cfg.max_project_cost_inr is not None and project_cost_inr > cfg.max_project_cost_inr:
        return False
    return True


def _closest_band(
    schemes: tuple[SihSchemeConfig, ...], project_cost_inr: Decimal
) -> SihSchemeConfig:
    """Every declared scheme is out of band for `project_cost_inr` — pick the
    one whose band is nearest, so the caller's own floor/ceiling finding
    fires against the most relevant declared config rather than an arbitrary
    one. Ties broken by table order (deterministic)."""

    def _distance(cfg: SihSchemeConfig) -> Decimal:
        lo = cfg.min_project_cost_inr if cfg.min_project_cost_inr is not None else Decimal("0")
        hi = cfg.max_project_cost_inr if cfg.max_project_cost_inr is not None else project_cost_inr
        if project_cost_inr < lo:
            return lo - project_cost_inr
        if project_cost_inr > hi:
            return project_cost_inr - hi
        return Decimal("0")  # pragma: no cover — would mean scheme_covers_project_cost lied

    return min(schemes, key=_distance)


def route_scheme(
    category: BusinessCategory,
    schemes: SihSchemeConfig | Sequence[SihSchemeConfig] | None,
    *,
    project_cost_inr: Decimal | None = None,
) -> SchemeRoutingResult:
    """Select a financing structure for `category` from `schemes`.

    `category` is accepted (not yet used) so a later per-category or
    per-state routing table is additive, not a signature change.

    With `project_cost_inr=None` (the presence-only gate `structure_financing`
    calls before a cost is derivable): the first declared scheme is selected,
    or `NOT_CONFIGURED` if none is declared — this call only ever decides
    "is anything declared", never which band.

    With `project_cost_inr` given: the declared scheme whose band contains it
    is selected; if none does, the nearest declared band is selected instead
    so the caller's own floor/ceiling finding can report the mismatch against
    a real, named scheme rather than silently picking nothing.
    """
    del category  # reserved for a future routing table; unused while routing is by cost band only
    ordered = normalize_schemes(schemes)
    if not ordered:
        return SchemeRoutingResult(
            status=SchemeRoutingStatus.NOT_CONFIGURED,
            reasons=["no scheme configuration is declared for this deployment"],
        )
    if project_cost_inr is None:
        chosen = ordered[0]
        reason = (
            f"{chosen.scheme_name} is the only declared financing structure"
            if len(ordered) == 1
            else f"{chosen.scheme_name} selected; no project cost was given yet to band-route by"
        )
        return SchemeRoutingResult(
            status=SchemeRoutingStatus.SELECTED, selected=chosen, reasons=[reason]
        )

    covering = [s for s in ordered if scheme_covers_project_cost(s, project_cost_inr)]
    if covering:
        chosen = covering[0]
        reason = f"{chosen.scheme_name} covers a project cost of Rs {project_cost_inr}"
    else:
        chosen = _closest_band(ordered, project_cost_inr)
        reason = (
            f"{chosen.scheme_name} is the closest declared band to a project cost of "
            f"Rs {project_cost_inr}; no declared scheme's band actually covers it"
        )
    return SchemeRoutingResult(
        status=SchemeRoutingStatus.SELECTED, selected=chosen, reasons=[reason]
    )


__all__ = [
    "SchemeRoutingResult",
    "SchemeRoutingStatus",
    "normalize_schemes",
    "route_scheme",
    "scheme_covers_project_cost",
]
