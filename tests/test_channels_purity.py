"""`channels/` is a translation boundary only (CLAUDE.md §4, §25 Phase 7's
"must NOT: business logic inside the channel layer"). It may call
`app/service.py` and use `app/dto.py`; it must never import an engine
(`market/`, `finance/`, `knowledge/`, `discovery/`), the conversation state
machine, the LLM package, a data source, a geocoder, the database, or an
HTTP client directly. Mirrors `tests/test_market_purity.py`'s AST check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vyaparsarathi.channels as channels_pkg

_CHANNELS_DIR = Path(channels_pkg.__file__).parent
_FORBIDDEN_PREFIXES = (
    "vyaparsarathi.market",
    "vyaparsarathi.finance",
    "vyaparsarathi.knowledge",
    "vyaparsarathi.discovery",
    "vyaparsarathi.conversation",
    "vyaparsarathi.llm",
    "vyaparsarathi.sources",
    "vyaparsarathi.geocoding",
    "vyaparsarathi.database",
    "vyaparsarathi.dedup",
    "vyaparsarathi.normalization",
    "httpx",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_channels_do_not_import_engines_or_io() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_CHANNELS_DIR.rglob("*.py")):
        bad = sorted(
            mod
            for mod in _imported_modules(py)
            for prefix in _FORBIDDEN_PREFIXES
            if mod == prefix or mod.startswith(prefix + ".")
        )
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"channels/ must not import engines or I/O layers: {offenders}"
