"""Environment-driven settings (CLAUDE.md §4.1, §24).

All values have safe defaults so the package imports and unit-tests run with no
``.env`` present. Secrets (none required in Phase 1) would live here too, never
inline in code.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vyaparsarathi.errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VYAPAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Overpass ---
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_mirrors: list[str] = Field(
        default_factory=lambda: [
            "https://overpass.kumi.systems/api/interpreter",
            "https://z.overpass-api.de/api/interpreter",
        ]
    )
    overpass_query_timeout_s: int = 60

    # --- Nominatim ---
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_min_interval_s: float = 1.0

    # --- Shared HTTP ---
    http_timeout_s: float = 60.0
    http_max_retries: int = 3
    http_backoff_base_s: float = 2.0
    user_agent: str = "VyaparSarathi/0.1 (SIH26091; contact: set-me@example.org)"

    # --- Caching ---
    cache_enabled: bool = True
    cache_dir: str = ".cache"
    cache_ttl_s: int = 7 * 24 * 3600

    # --- Discovery limits ---
    max_radius_m: int = 25_000

    # --- Phase 2C demand signals ---
    # Pre-joined Census 2011 village extract (CSV or CSV.gz). See
    # data/demand/SOURCES.md. A missing file degrades to "no population data".
    census_villages_path: str = "data/demand/census2011_villages.csv.gz"

    # --- Phase 5 knowledge corpus ---
    # Directory holding documents.jsonl, chunks.jsonl(.gz), parameters.csv and
    # manifest.json. See data/knowledge/SOURCES.md. A missing directory
    # degrades to "no evidence available" (KnowledgeAcquisitionReport.
    # corpus_present=False), never a crash.
    knowledge_corpus_dir: str = "data/knowledge"
    # How many RetrievedPassage objects a query may return for display/citation.
    knowledge_max_passages: int = 8

    # --- Deduplication thresholds (CLAUDE.md §9) ---
    dedup_name_similarity_merge: float = 87.0
    dedup_name_similarity_uncertain: float = 75.0
    dedup_distance_merge_m: float = 120.0
    dedup_distance_uncertain_m: float = 200.0

    # --- Persistence ---
    db_url: str = "sqlite:///vyaparsarathi.sqlite3"

    # --- Logging ---
    log_level: str = "INFO"

    # --- Phase 6: LLM orchestration (CLAUDE.md §4.1: "an LLM client library
    # ... chosen in Phases 6-7, not before" — none is; this is a thin httpx
    # adapter). `llm_enabled=False` is a fully supported mode: the whole
    # pipeline runs and answers deterministically with no API key. ---
    llm_enabled: bool = False
    llm_base_url: str = ""  # empty = disabled; no default endpoint is assumed
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2
    llm_backoff_base_s: float = 1.0
    llm_max_output_tokens: int = 1024
    llm_temperature: float = 0.2
    llm_prompt_version: str = "v1"

    @field_validator("llm_base_url")
    @classmethod
    def _no_credential_in_url(cls, v: str) -> str:
        """`utils/http.py` logs the request URL at WARNING and interpolates
        it into `HttpError`'s message on failure — a credential in the query
        string would leak into logs and exceptions. The key belongs only in
        a header, set once by `llm/provider.py::HttpLlmProvider.__init__`."""
        lowered = v.lower()
        if any(marker in lowered for marker in ("key=", "token=", "apikey=", "secret=")):
            raise ConfigError(
                "VYAPAR_LLM_BASE_URL must not embed a credential in the query string; "
                "set VYAPAR_LLM_API_KEY instead"
            )
        return v

    @field_validator("overpass_mirrors", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @property
    def overpass_endpoints(self) -> list[str]:
        """Primary endpoint first, then de-duplicated mirrors."""
        seen: set[str] = set()
        ordered: list[str] = []
        for url in [self.overpass_url, *self.overpass_mirrors]:
            if url and url not in seen:
                seen.add(url)
                ordered.append(url)
        return ordered


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached process-wide settings instance."""
    return Settings()
