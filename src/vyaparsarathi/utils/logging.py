"""Logging setup (CLAUDE.md §33).

Stdlib ``logging`` only. One logger per module via ``get_logger(__name__)``.
Never log secrets or full user messages at INFO.
"""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Idempotently configure root logging for CLI / app use."""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(level.upper())
        return
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
