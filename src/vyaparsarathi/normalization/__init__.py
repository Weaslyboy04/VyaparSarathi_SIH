"""Raw source records -> :class:`NormalizedBusiness` (CLAUDE.md §7, §8).

Downstream code never sees an OSM tag. Name text is normalised for *matching*
only; the human-readable ``name`` is preserved untouched.
"""

from vyaparsarathi.normalization.business import NormalizedOsm, normalize_osm_element
from vyaparsarathi.normalization.text import normalize_name

__all__ = ["normalize_name", "normalize_osm_element", "NormalizedOsm"]
