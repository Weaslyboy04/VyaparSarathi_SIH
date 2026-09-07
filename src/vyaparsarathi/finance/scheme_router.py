"""Deterministic scheme selection (CLAUDE.md §4's separate "scheme / credit
routing engine"; Tier 1 "Deterministic Scheme Router").

`docs/phase-5.md` is explicit that Phase 5's resolver is *not* a scheme
router — "it does not decide which scheme applies." This module is that
router, at the smallest scope Tier 1 needs: exactly one financing structure
is declared (`config/sih_scheme.py::DEFAULT_SIH_SCHEME_CONFIG`), so routing
today is a presence check, not a comparison. The result shape
(`status`/`selected`/`reasons`) is what a later multi-scheme table would
extend without changing any caller.

PURE. No I/O, no clock, no randomness — a router over declared configuration
is deterministic by construction.
"""

from __future__ import annotations

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


def route_scheme(
    category: BusinessCategory, scheme_cfg: SihSchemeConfig | None
) -> SchemeRoutingResult:
    """Select a financing structure for `category`. Tier 1 declares exactly
    one; `category` is accepted (not yet used) so a later per-category or
    per-state routing table is additive, not a signature change."""
    del (
        category
    )  # reserved for a future routing table; unused while exactly one structure is declared
    if scheme_cfg is None:
        return SchemeRoutingResult(
            status=SchemeRoutingStatus.NOT_CONFIGURED,
            reasons=["no scheme configuration is declared for this deployment"],
        )
    return SchemeRoutingResult(
        status=SchemeRoutingStatus.SELECTED,
        selected=scheme_cfg,
        reasons=[f"{scheme_cfg.scheme_name} is the only declared financing structure"],
    )


__all__ = ["SchemeRoutingResult", "SchemeRoutingStatus", "route_scheme"]
