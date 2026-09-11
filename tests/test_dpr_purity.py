"""The DPR assembly path must not re-run an engine, read a clock, hit the
network, or call an LLM (CLAUDE.md §23, §25 Phase 8, §28). AST checks,
mirroring `tests/test_market_purity.py` / `tests/test_conversation_purity.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vyaparsarathi.dpr as dpr_pkg

_DPR_DIR = Path(dpr_pkg.__file__).parent

# Modules that build the structured document. `render_pdf.py` (reportlab) and
# `service.py` (file I/O) are deliberately excluded — they are the rendering /
# output edge, not the deterministic assembler.
_PURE_MODULES = (
    "assemble.py",
    "sections.py",
    "fingerprint.py",
    "format.py",
    "provenance.py",
    "slots.py",
    "artifacts.py",
    "citations.py",
    "report_models.py",
    "disclaimers.py",
)

_FORBIDDEN_IMPORT_PREFIXES = (
    "httpx",
    "reportlab",
    "vyaparsarathi.llm",
    "vyaparsarathi.sources",
    "vyaparsarathi.geocoding",
    "vyaparsarathi.discovery",
    "vyaparsarathi.database",
    # engine *functions* (their result models live in separate *_models modules,
    # which are allowed). finance.capacity is the one module that ships a model
    # and its function together and is imported for the model only.
    "vyaparsarathi.finance.assessment",
    "vyaparsarathi.finance.structuring",
    "vyaparsarathi.finance.costs",
    "vyaparsarathi.finance.cashflow",
    "vyaparsarathi.finance.debt",
    "vyaparsarathi.finance.dscr",
    "vyaparsarathi.finance.stress",
    "vyaparsarathi.finance.pipeline",
    "vyaparsarathi.finance.scheme_router",
    "vyaparsarathi.market.opportunity",
    "vyaparsarathi.market.assessment",
    "vyaparsarathi.market.metrics",
    "vyaparsarathi.market.demand",
    "vyaparsarathi.market.classifier",
    "vyaparsarathi.market.proposed",
    "vyaparsarathi.market.relationships",
    "vyaparsarathi.knowledge.resolver",
    "vyaparsarathi.knowledge.plan_binding",
    "vyaparsarathi.knowledge.retrieval",
    "vyaparsarathi.knowledge.confidence",
)

_CLOCK_CALLS = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("date", "today"),
    ("time", "time"),
    ("time", "monotonic"),
}


def _module_paths() -> list[Path]:
    return [_DPR_DIR / name for name in _PURE_MODULES]


def _imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_pure_dpr_modules_do_not_import_engines_io_or_llm() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _module_paths():
        bad = sorted(
            mod
            for mod in _imports(ast.parse(path.read_text(encoding="utf-8")))
            for prefix in _FORBIDDEN_IMPORT_PREFIXES
            if mod == prefix or mod.startswith(prefix + ".")
        )
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"pure DPR modules import forbidden layers: {offenders}"


def test_pure_dpr_modules_do_not_read_a_clock() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _module_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = node.func.value
                if isinstance(base, ast.Name) and (base.id, node.func.attr) in _CLOCK_CALLS:
                    hits.append(f"{base.id}.{node.func.attr}()")
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"pure DPR modules read a clock: {offenders}"


def test_assemble_is_read_only_over_the_session() -> None:
    """Assembling a report must not mutate the session it was given."""
    import tempfile
    from datetime import UTC, datetime

    from tests.dpr_pipeline import full_scenario_turns, run_pipeline
    from vyaparsarathi.dpr.assemble import assemble_report

    tmp = Path(tempfile.mkdtemp())
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp)
    before = session.model_dump_json()
    assemble_report(session, generated_at=datetime(2026, 1, 1, tzinfo=UTC))
    assemble_report(session, generated_at=datetime(2027, 6, 6, tzinfo=UTC))
    assert session.model_dump_json() == before


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
