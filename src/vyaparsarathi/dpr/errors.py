"""DPR-generation exceptions (CLAUDE.md §33, §25 Phase 8)."""

from __future__ import annotations

from vyaparsarathi.errors import VyaparError


class DprError(VyaparError):
    """DPR assembly or rendering could not proceed — e.g. the requested
    session does not exist. A missing artifact is never this: an absent
    engine result becomes a worded evidence gap inside the report, not an
    error."""


class DprOutputExistsError(DprError):
    """An output path already exists and ``overwrite`` was not set. The DPR
    service never replaces a file silently (Phase 8 requirement)."""


__all__ = ["DprError", "DprOutputExistsError"]
