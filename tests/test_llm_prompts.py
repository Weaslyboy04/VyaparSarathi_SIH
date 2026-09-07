"""`llm/prompts.py` (CLAUDE.md §25 Phase 6)."""

from __future__ import annotations

import re

import pytest

from vyaparsarathi.llm.prompts import PROMPT_REGISTRY

_DIGIT_RUN = re.compile(r"\d{3,}")


def test_prompt_ids_are_unique() -> None:
    assert len(PROMPT_REGISTRY) == len(set(PROMPT_REGISTRY))


@pytest.mark.parametrize("prompt_id", sorted(PROMPT_REGISTRY))
def test_no_prompt_hardcodes_a_number(prompt_id: str) -> None:
    body = PROMPT_REGISTRY[prompt_id]
    assert not _DIGIT_RUN.search(body), f"prompt {prompt_id!r} contains a digit run >= 3: {body!r}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
