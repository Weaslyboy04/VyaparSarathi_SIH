"""Phase 1 orchestration: geocode -> fetch -> normalize -> dedup -> store -> result.

See CLAUDE.md §26.1. This is the single entry point Phase 2 will consume.
"""

from vyaparsarathi.discovery.demand_acquisition import acquire_demand_evidence
from vyaparsarathi.discovery.service import DiscoveryService, build_default_service

__all__ = ["DiscoveryService", "build_default_service", "acquire_demand_evidence"]
