"""`llm/` and `app/` are leaves: no Phase 1-5 engine package may import
either (CLAUDE.md §25 Phase 6) — the model/engine layers must stay usable
with zero LLM code ever loaded."""

from __future__ import annotations

import ast
from pathlib import Path

import vyaparsarathi as pkg

_SRC_DIR = Path(pkg.__file__).parent
_ENGINE_DIRS = (
    "market",
    "finance",
    "knowledge",
    "discovery",
    "sources",
    "geocoding",
    "database",
    "models",
)
_FORBIDDEN_PREFIXES = ("vyaparsarathi.llm", "vyaparsarathi.app")


def _imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_no_engine_package_imports_llm_or_app() -> None:
    offenders: dict[str, list[str]] = {}
    for engine_dir in _ENGINE_DIRS:
        for py in sorted((_SRC_DIR / engine_dir).rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            imported = _imported_modules(tree)
            bad = sorted(
                mod
                for mod in imported
                for prefix in _FORBIDDEN_PREFIXES
                if mod == prefix or mod.startswith(prefix + ".")
            )
            if bad:
                offenders[f"{engine_dir}/{py.name}"] = bad
    assert not offenders, f"engine packages must not import llm/ or app/: {offenders}"


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
