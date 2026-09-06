"""The Phase 3 acquisition -> engine seam (CLAUDE.md §12, §33).

:class:`OpportunityEvidence` is to Phase 3 what :class:`DemandEvidence` is to
Phase 2C: the single JSON-round-trippable object that crosses from the impure
acquisition layer (``discovery/opportunity_acquisition.py`` — network, disk) into
the pure scoring engine (``market/opportunity.py`` — no I/O). It bundles:

* one **union** :class:`DiscoveryResult` covering every candidate category in a
  single Overpass fetch (so the engine re-classifies per candidate rather than
  re-querying — see ``market/opportunity.py``);
* one :class:`DemandEvidence` for the same catchment (category-agnostic, gathered
  once);
* the ordered ``candidate_categories`` to score;
* ``per_category_confidence`` — a data-coverage confidence computed **per
  candidate** in the acquisition layer.

Why ``per_category_confidence`` exists: :func:`coverage_confidence` was
implicitly per-category in Phase 1 because the fetch itself was one category. A
union fetch of ~8 categories would otherwise inflate the single
``DiscoveryResult.confidence`` for *every* candidate — including ones with zero
relevant businesses — which would stop Phase 2D's "absence of evidence" rung
from firing and turn an honest *insufficient_evidence* into a flattering
*underserved*. The acquisition layer restores the original semantics by
computing a coverage confidence per candidate (over only the businesses that are
not irrelevant to that candidate); the engine swaps it onto a per-candidate view
of the discovery result before running Phase 2B. No Phase 1/2 code changes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.demand import DemandEvidence
from vyaparsarathi.models.results import DiscoveryResult
from vyaparsarathi.models.taxonomy import BusinessCategory


class OpportunityEvidence(BaseModel):
    """Pure input to :func:`vyaparsarathi.market.opportunity.score_opportunities`."""

    model_config = ConfigDict(extra="forbid")

    discovery: DiscoveryResult
    demand: DemandEvidence

    # Ordered, de-duplicated. The proposed category is always present.
    candidate_categories: list[BusinessCategory] = Field(default_factory=list)

    # category -> data-coverage confidence for that candidate (0..1). Absent keys
    # fall back to ``discovery.confidence`` in the engine.
    per_category_confidence: dict[BusinessCategory, float] = Field(default_factory=dict)

    warnings: list[str] = Field(default_factory=list)
