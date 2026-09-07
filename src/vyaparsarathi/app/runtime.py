"""Builds and owns the impure handles a turn needs (CLAUDE.md §25 Phase 6).

`AdvisoryRuntime` is where every network/disk client is actually
constructed — `app/service.py` never reads `Settings` directly and a channel
never constructs an engine. One instance is built per process (or per demo
run) and its handles are shared across every session, exactly as
`scripts/*_demo.py` already share one `OverpassClient`/`CensusVillageSource`
across scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.conversation.conversation_config import (
    DEFAULT_CONVERSATION_CONFIG,
    ConversationConfig,
)
from vyaparsarathi.database import InMemoryBusinessRepository
from vyaparsarathi.database.repository import BusinessRepository
from vyaparsarathi.discovery.service import DiscoveryService
from vyaparsarathi.geocoding.base import Geocoder
from vyaparsarathi.geocoding.nominatim import NominatimGeocoder
from vyaparsarathi.knowledge.base import CorpusStore, Retriever
from vyaparsarathi.knowledge.retrieval import LexicalRetriever
from vyaparsarathi.llm.provider import HttpLlmProvider, LlmProvider
from vyaparsarathi.llm.tools import RunContext
from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore
from vyaparsarathi.sources.osm.adapter import OverpassSource
from vyaparsarathi.sources.osm.client import OverpassClient
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AdvisoryRuntime:
    settings: Settings
    run_context: RunContext
    geocoder: Geocoder
    overpass_client: OverpassClient
    # `None` when `settings.llm_enabled` is False — the fully supported,
    # zero-API-key mode (CLAUDE.md §3.1). `app/service.py` checks this, not
    # `settings.llm_enabled` directly, so a runtime built without a key
    # never accidentally attempts a call.
    llm_provider: LlmProvider | None = None

    def close(self) -> None:
        self.overpass_client.close()
        close = getattr(self.geocoder, "close", None)
        if callable(close):
            close()
        provider_close = getattr(self.llm_provider, "close", None)
        if callable(provider_close):
            provider_close()


def build_default_runtime(
    settings: Settings | None = None,
    *,
    repository: BusinessRepository | None = None,
    conv_cfg: ConversationConfig = DEFAULT_CONVERSATION_CONFIG,
) -> AdvisoryRuntime:
    """Wire the standard stack: Nominatim + a shared Overpass client + the
    Census extract + the Phase 5 corpus (+ a lexical retriever only when a
    corpus is actually present — CLAUDE.md §3.1: no evidence, no retrieval
    theatre) + an in-memory business repository."""
    s = settings or get_settings()
    geocoder = NominatimGeocoder(s)
    overpass_client = OverpassClient(s)
    discovery_source = OverpassSource(s, client=overpass_client)
    repo = repository or InMemoryBusinessRepository()
    discovery_service = DiscoveryService(
        geocoder=geocoder, source=discovery_source, repository=repo, settings=s
    )
    census = CensusVillageSource(settings=s)
    corpus: CorpusStore = FileCorpusStore(settings=s)
    retriever: Retriever | None = (
        LexicalRetriever(corpus) if corpus.report().corpus_present else None
    )
    if retriever is None:
        logger.info("no knowledge corpus present; passage retrieval is disabled for this runtime")

    ctx = RunContext(
        settings=s,
        discovery_service=discovery_service,
        overpass_client=overpass_client,
        census=census,
        corpus=corpus,
        repository=repo,
        retriever=retriever,
        conv_cfg=conv_cfg,
    )
    llm_provider: LlmProvider | None = HttpLlmProvider(s) if s.llm_enabled else None
    return AdvisoryRuntime(
        settings=s,
        run_context=ctx,
        geocoder=geocoder,
        overpass_client=overpass_client,
        llm_provider=llm_provider,
    )


__all__ = ["AdvisoryRuntime", "build_default_runtime"]
