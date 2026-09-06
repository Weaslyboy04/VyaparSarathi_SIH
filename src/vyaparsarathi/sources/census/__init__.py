"""Census of India 2011 village-level population extract (CLAUDE.md §6, §23).

A local, offline reference dataset — the only village-resolution population source
that exists for rural India. Coordinates are pre-joined **by census code** in
``scripts/build_census_dataset.py`` so the running system never matches names.
See ``data/demand/SOURCES.md`` for exact provenance and state coverage.
"""

from vyaparsarathi.sources.census.loader import (
    CensusNearResult,
    CensusVillageRow,
    CensusVillageSource,
)

__all__ = ["CensusNearResult", "CensusVillageRow", "CensusVillageSource"]
