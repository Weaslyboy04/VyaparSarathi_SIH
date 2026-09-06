"""The entrepreneur profile (CLAUDE.md §13, §14).

Phase 3 input. Everything the person tells us about their resources, collected
over the conversation and treated as **user-provided, unverified** data
(``unverified=True``, labelled as such in every downstream output and the DPR).

The §13 / §14 separations are enforced by *type*, not by comment:

* ``liquid_cash_inr`` is liquid money, on its own. There is **no** field that
  sums it with anything, and assets carry **no** monetary value — cash and
  physical assets "are tracked separately and never silently summed" (§13).
* There is no ``promoter_margin`` / ``scheme_eligible`` field. Whether an asset
  satisfies a scheme's promoter-contribution rule is a *retrieved rule* (§14,
  §18) decided by Phase 4/5 — Phase 3 cannot express it, so it must not imply
  it.

Assets are a **set of kinds** with optional free-text notes; owning an asset can
later reduce a project's real spend (§14) without counting as margin.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.taxonomy import BusinessCategory


class AssetKind(StrEnum):
    """Physical assets a rural entrepreneur might already own (§13). Coarse on
    purpose — this is a capability signal, not an inventory."""

    STOREFRONT = "storefront"  # a shop / pukka room usable for retail
    LAND = "land"
    VEHICLE = "vehicle"  # cart, two-wheeler, tempo, tractor-trailer
    EQUIPMENT = "equipment"  # machinery / tools already owned
    LIVESTOCK = "livestock"
    COLD_STORAGE = "cold_storage"  # a working chiller / cold room
    WAREHOUSE = "warehouse"  # covered dry storage / godown


class EntrepreneurProfile(BaseModel):
    """User-provided, unverified. Consumed by
    :func:`vyaparsarathi.market.opportunity.score_opportunities`."""

    model_config = ConfigDict(extra="forbid")

    # Liquid money, on its own (§13). Rupees. `None` = not stated (never 0).
    liquid_cash_inr: int | None = Field(default=None, ge=0)

    # Physical assets already owned, as kinds. No monetary value is attached and
    # nothing sums these with `liquid_cash_inr` (§13, §14).
    assets: set[AssetKind] = Field(default_factory=set)
    asset_notes: dict[AssetKind, str] = Field(default_factory=dict)

    # Relevant trade experience, as internal categories, plus a rough total.
    experience_categories: set[BusinessCategory] = Field(default_factory=set)
    years_experience: int | None = Field(default=None, ge=0)
    skills: set[str] = Field(default_factory=set)

    # The business the person actually proposed, mapped onto the internal
    # taxonomy. ``proposed_category`` is ``None`` when nothing specific was
    # proposed OR when the free text could not be resolved to one category — in
    # both cases the engine ranks the shortlist and makes no
    # proposed-vs-alternative call. ``proposed_raw_text`` keeps the original
    # phrasing for the DPR. (Phase 3 compares at category granularity, so
    # ``proposed_subtypes`` is carried but not used in scoring.)
    #
    # This is category + strings rather than a ``market.ProposedBusiness`` object
    # on purpose: the base ``models/`` layer must not import ``market/`` (that
    # package eagerly imports the engines, which import this module — a cycle).
    proposed_category: BusinessCategory | None = None
    proposed_subtypes: list[str] = Field(default_factory=list)
    proposed_raw_text: str | None = None

    # Free-text obligations / limits the person stated ("cannot travel", "must
    # keep the existing tailoring income"). Carried through, not interpreted.
    constraints: list[str] = Field(default_factory=list)

    # This whole record is self-reported and unverified (§13). Kept as a field so
    # every consumer and the DPR can caption it.
    unverified: bool = True
