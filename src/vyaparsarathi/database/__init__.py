"""Persistence (CLAUDE.md §4, §10, §26 STEP 11-12).

The discovery service depends only on :class:`BusinessRepository`. Two
implementations ship:

* :class:`InMemoryBusinessRepository` — default for the CLI and tests.
* :class:`SqlBusinessRepository` — SQLAlchemy 2.x, SQLite by default, any
  SQLAlchemy URL (incl. PostgreSQL) via ``VYAPAR_DB_URL``.

Radius / nearest-neighbour filtering is done in Python (haversine) for Phase 1.
Swapping in PostGIS geometry columns + a GiST index is a later, contained change
behind the same interface.
"""

from vyaparsarathi.database.memory import InMemoryBusinessRepository
from vyaparsarathi.database.repository import BusinessRepository
from vyaparsarathi.database.sql import SqlBusinessRepository, create_repository

__all__ = [
    "BusinessRepository",
    "InMemoryBusinessRepository",
    "SqlBusinessRepository",
    "create_repository",
]
