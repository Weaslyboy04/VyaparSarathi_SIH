"""The ``knowledge/`` package must stay pure (CLAUDE.md §3.2, §18, §28;
mirrors ``tests/test_finance_purity.py``).

Every engine under ``knowledge/`` consumes only its own input models
(``models/parameters.py``, ``models/knowledge.py``) plus, where needed,
``models/taxonomy.py`` — and it must never import a source adapter, a
geocoder, the database, the discovery acquisition layer, or
`httpx`/`random`/`time`, nor read the wall clock or an unseeded random/UUID
source at call time. The one declared exception is ``knowledge/
plan_binding.py``, which is allowed (and only it) to construct a
`vyaparsarathi.models.finance.FinancialInput` — the Phase 4 seam. This is the
structural guarantee behind the module's own docstring claim: nothing in
``knowledge/`` other than ``plan_binding.py`` can put a number in front of the
financial engine.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vyaparsarathi.knowledge as knowledge_pkg

_KNOWLEDGE_DIR = Path(knowledge_pkg.__file__).parent
_FORBIDDEN_IMPORT_PREFIXES = (
    "vyaparsarathi.finance",
    "vyaparsarathi.sources",
    "vyaparsarathi.geocoding",
    "vyaparsarathi.database",
    "vyaparsarathi.discovery",
    "httpx",
    "random",
    "time",
)
# Only this module may construct a FinancialInput — the Phase 4 seam.
_FINANCIAL_INPUT_CONSTRUCTION_ALLOWED_IN = {"plan_binding.py"}

_FORBIDDEN_CALL_NAMES = {"now", "today", "uuid4"}
_FINANCIAL_MODEL_CONSTRUCTORS = {
    "FinancialInput",
    "FinancialPlanInput",
    "LoanTerms",
    "FinancingInput",
    "CostLine",
    "OpexLine",
}


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


def _financial_model_constructions(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if name in _FINANCIAL_MODEL_CONSTRUCTORS:
                found.add(name)
    return found


def test_knowledge_modules_do_not_import_io_layers() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_KNOWLEDGE_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        imported = _imported_modules(tree)
        bad = sorted(
            mod
            for mod in imported
            for prefix in _FORBIDDEN_IMPORT_PREFIXES
            if mod == prefix or mod.startswith(prefix + ".")
        )
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"knowledge/ must not import I/O layers: {offenders}"


def test_knowledge_modules_never_read_the_clock_or_an_unseeded_random_source() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_KNOWLEDGE_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        bad = sorted(_forbidden_calls(tree))
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"knowledge/ must not read a clock or RNG at call time: {offenders}"


def test_only_plan_binding_constructs_financial_engine_input_models() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_KNOWLEDGE_DIR.rglob("*.py")):
        if py.name in _FINANCIAL_INPUT_CONSTRUCTION_ALLOWED_IN:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        bad = sorted(_financial_model_constructions(tree))
        if bad:
            offenders[py.name] = bad
    assert not offenders, (
        f"only {sorted(_FINANCIAL_INPUT_CONSTRUCTION_ALLOWED_IN)} may construct a Phase 4 "
        f"financial-engine input model: {offenders}"
    )


def test_plan_binding_is_the_only_module_that_needs_the_exception() -> None:
    # A living check that the allow-list isn't stale: plan_binding.py really
    # does construct at least one financial model (else the exception would
    # be dead weight and this test's own intent unverifiable).
    path = _KNOWLEDGE_DIR / "plan_binding.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert _financial_model_constructions(tree), (
        "plan_binding.py no longer constructs any financial model — either the "
        "exception in _FINANCIAL_INPUT_CONSTRUCTION_ALLOWED_IN can be dropped, or "
        "something moved without updating this test"
    )


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
