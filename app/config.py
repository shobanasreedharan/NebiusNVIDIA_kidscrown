from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

MAX_FAMILY_POLICY_CHARS = 2000
MAX_CHILD_POLICY_CHARS = 2000
MAX_CHANNEL_RULE_NOTE_CHARS = 200
MAX_BLOCKED_WORD_ITEMS = 100
MAX_BLOCKED_WORD_CHARS = 50
MAX_EFFECTIVE_POLICY_CHARS = 4500


class Settings(BaseSettings):
    database_url: str = "sqlite:///./kidscrown.db"
    nebius_api_key: str | None = None
    youtube_api_key: str | None = None
    tavily_api_key: str | None = None
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nebius_model: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    classification_confidence_threshold: float = 0.7
    cache_ttl_seconds: int = 7 * 24 * 60 * 60
    metadata_cache_ttl_seconds: int | None = None
    transcript_cache_ttl_seconds: int | None = None
    classification_cache_ttl_seconds: int | None = None
    redis_url: str | None = None          # unset = Redis layer disabled
    redis_enabled: bool = False            # defaults to true when redis_url is configured
    redis_cache_tz: str = "America/Chicago"
    redis_connect_timeout_seconds: float = 0.3
    redis_socket_timeout_seconds: float = 0.3
    redis_circuit_breaker_seconds: float = 60.0
    youtube_connect_timeout_seconds: float = 4.0
    youtube_read_timeout_seconds: float = 8.0
    youtube_pool_timeout_seconds: float = 4.0
    youtube_identify_total_timeout_seconds: float = 12.0
    request_body_max_bytes: int = Field(default=64 * 1024, ge=1)
    classify_identify_requests_per_family_per_hour: int = Field(default=30, ge=1)
    # Short TTL for transient transcript-fetch failures (network/rate-limit),
    # kept separate from transcript_cache_ttl_seconds which covers a
    # confirmed "no captions on this video" result. Tavily-backed fetches
    # reuse both of these -- no separate TTL needed for that source.
    transcript_failure_cache_ttl_seconds: int = 60 * 60
    disabled_rule_layers: str = ""
    block_age_restricted: bool = True
    default_never_words_enabled: bool = True
    trusted_channels_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _default_redis_enabled(cls, values):
        if isinstance(values, dict) and "redis_enabled" not in values:
            values["redis_enabled"] = bool(values.get("redis_url"))
        return values

    # Resolves a cache-specific TTL or falls back to the default TTL.
    def ttl_for(self, cache_type: str) -> int:
        configured = getattr(self, f"{cache_type}_cache_ttl_seconds", None)
        return configured if configured is not None else self.cache_ttl_seconds

    @property
    def redis_is_enabled(self) -> bool:
        return bool(self.redis_url) and self.redis_enabled


# Loads and caches application settings from the environment.
@lru_cache
def get_settings() -> Settings:
    return Settings()
