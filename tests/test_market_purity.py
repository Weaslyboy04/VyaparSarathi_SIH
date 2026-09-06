"""The ``market/`` package must stay pure (CLAUDE.md §3.2, §11; Phase 2C design).

Every engine under ``market/`` consumes only Phase 1 / 2A / 2C result objects.
It must never import a source adapter, a geocoder, an HTTP client, or the
database — that is what keeps the acquisition/calculation seam real rather than a
naming convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vyaparsarathi.market as market_pkg

_MARKET_DIR = Path(market_pkg.__file__).parent
_FORBIDDEN_PREFIXES = (
    "vyaparsarathi.sources",
    "vyaparsarathi.geocoding",
    "vyaparsarathi.database",
    "vyaparsarathi.discovery",
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


def test_market_modules_do_not_import_io_layers() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_MARKET_DIR.rglob("*.py")):
        bad = sorted(
            mod
            for mod in _imported_modules(py)
            for prefix in _FORBIDDEN_PREFIXES
            if mod == prefix or mod.startswith(prefix + ".")
        )
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"market/ must not import I/O layers: {offenders}"
