"""Per-source category mappings (CLAUDE.md §8).

This package is the *only* place that knows external tag vocabularies. Two
directions are needed:

* :mod:`vyaparsarathi.categories.osm_map` — an OSM element's tags -> our category
  (used during normalization).
* :mod:`vyaparsarathi.categories.osm_query_tags` — our category -> the OSM tag
  selectors worth querying for it (used to build the Overpass request).
"""
