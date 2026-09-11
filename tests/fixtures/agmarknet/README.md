# AGMARKNET fixtures

`pulses_bihar.json`'s **shape** (the `{total, count, limit, offset, records:
[...]}` envelope and every field name: `state`, `district`, `market`,
`commodity`, `variety`, `grade`, `arrival_date`, `min_price`, `max_price`,
`modal_price`) was verified against a real, live call to
`https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070`
during implementation planning.

The specific **values** in this file are a hand-authored, realistic sample
(not a byte-for-byte live capture) — two later attempts to capture one during
implementation hit transient network failures (a `504` from the shared demo
API key, then a connection failure) in this environment. They exist only to
exercise the client's parsing/aggregation logic; they must never be read as
real prices or treated as authoritative for `market/commodity_map.py`'s
category→commodity table.

Before treating that mapping table as final, do the live-API verification
pass the implementation plan calls for: query the real endpoint, dump the
distinct `commodity` values for the categories in use, and cross-check the
spellings/parenthetical forms (e.g. `"Arhar (Tur/Red Gram)(Whole)"`) used
here against them — a single wrong character silently returns zero records.

`empty.json` is the "reachable, zero records" case (mirrors
`tests/fixtures/osm/empty.json`).
