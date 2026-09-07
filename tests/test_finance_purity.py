"""The ``finance/`` package must stay pure (CLAUDE.md §3.2, §15, §28; mirrors
``tests/test_market_purity.py``).

Every engine under ``finance/`` consumes only its own input models
(``models/finance.py``) plus, where needed, ``models/profile.py`` and
``models/taxonomy.py``. It must never import a source adapter, a geocoder, an
HTTP client, the database, or `random`/`time` — and it must never read the
wall clock or an unseeded random/UUID source at call time. The one declared
exception is ``finance/fit.py``, which is allowed (and only it) to import
``vyaparsarathi.market`` to hand a finished result back across the Phase 3
seam.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vyaparsarathi.finance as finance_pkg

_FINANCE_DIR = Path(finance_pkg.__file__).parent
_FORBIDDEN_IMPORT_PREFIXES = (
    "vyaparsarathi.sources",
    "vyaparsarathi.geocoding",
    "vyaparsarathi.database",
    "vyaparsarathi.discovery",
    "httpx",
    "random",
    "time",
)
# Only this module may import vyaparsarathi.market (the Phase 3 handoff).
_MARKET_IMPORT_ALLOWED_IN = {"fit.py"}

_FORBIDDEN_CALL_NAMES = {"now", "today", "uuid4"}


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _forbidden_calls(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _FORBIDDEN_CALL_NAMES:
                found.add(name)
    return found


def test_finance_modules_do_not_import_io_layers() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_FINANCE_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        imported = _imported_modules(tree)
        bad = sorted(
            mod
            for mod in imported
            for prefix in _FORBIDDEN_IMPORT_PREFIXES
            if mod == prefix or mod.startswith(prefix + ".")
        )
        if py.name not in _MARKET_IMPORT_ALLOWED_IN:
            bad += sorted(
                mod
                for mod in imported
                if mod == "vyaparsarathi.market" or mod.startswith("vyaparsarathi.market.")
            )
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"finance/ must not import I/O layers: {offenders}"


def test_finance_modules_never_read_the_clock_or_an_unseeded_random_source() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_FINANCE_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        bad = sorted(_forbidden_calls(tree))
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"finance/ must not read a clock or RNG at call time: {offenders}"


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
