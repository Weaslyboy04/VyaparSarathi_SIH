"""The ``conversation/`` package must stay pure (CLAUDE.md §3.2, §25 Phase 6;
mirrors ``tests/test_finance_purity.py`` / ``tests/test_market_purity.py``).

Every module under ``conversation/`` is a deterministic state machine over
already-acquired evidence. It must never import a source adapter, a
geocoder, an HTTP client, the database, the discovery orchestrator, or the
LLM package — ``llm/tools.py`` is the only module that executes a `StepId`.
It must never read the wall clock, an unseeded random/UUID source, or touch
the filesystem at call time. And only ``plan_builder.py`` may construct a
`FinancialInput` or an `EntrepreneurProfile` — the seam
``knowledge/plan_binding.py`` uses for `FinancialInput`, generalised.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vyaparsarathi.conversation as conversation_pkg

_CONVERSATION_DIR = Path(conversation_pkg.__file__).parent

_FORBIDDEN_IMPORT_PREFIXES = (
    "vyaparsarathi.llm",
    "vyaparsarathi.sources",
    "vyaparsarathi.geocoding",
    "vyaparsarathi.database",
    "vyaparsarathi.discovery",
    "httpx",
    "random",
    "time",
    "os",
    "pathlib",
)

_FORBIDDEN_CALL_NAMES = {"now", "today", "uuid4", "open"}

_FINANCIAL_INPUT_ALLOWED_IN = {"plan_builder.py"}
_PROFILE_ALLOWED_IN = {"plan_builder.py"}


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


def _constructor_calls(tree: ast.AST, class_name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == class_name:
                return True
    return False


def test_conversation_modules_do_not_import_io_layers() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_CONVERSATION_DIR.rglob("*.py")):
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
    assert not offenders, f"conversation/ must not import I/O layers: {offenders}"


def test_conversation_modules_never_read_the_clock_or_an_unseeded_random_source() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(_CONVERSATION_DIR.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        bad = sorted(_forbidden_calls(tree))
        if bad:
            offenders[py.name] = bad
    assert not offenders, (
        f"conversation/ must not read a clock/RNG/filesystem at call time: {offenders}"
    )


def test_only_plan_builder_constructs_a_financial_input() -> None:
    offenders: list[str] = []
    for py in sorted(_CONVERSATION_DIR.rglob("*.py")):
        if py.name in _FINANCIAL_INPUT_ALLOWED_IN:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        if _constructor_calls(tree, "FinancialInput"):
            offenders.append(py.name)
    assert not offenders, f"only plan_builder.py may construct a FinancialInput: {offenders}"


def test_only_plan_builder_constructs_an_entrepreneur_profile() -> None:
    offenders: list[str] = []
    for py in sorted(_CONVERSATION_DIR.rglob("*.py")):
        if py.name in _PROFILE_ALLOWED_IN:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        if _constructor_calls(tree, "EntrepreneurProfile"):
            offenders.append(py.name)
    assert not offenders, f"only plan_builder.py may construct an EntrepreneurProfile: {offenders}"


def test_conversation_package_anti_staleness() -> None:
    """A file must actually exist where these checks look, or the checks
    above are silently vacuous (the `test_knowledge_purity.py:122-132`
    pattern)."""
    files = list(_CONVERSATION_DIR.rglob("*.py"))
    assert len(files) >= 10
    assert (_CONVERSATION_DIR / "plan_builder.py") in files


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
