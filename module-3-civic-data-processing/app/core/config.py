"""Application configuration loaded from the environment / `.env` file.

Every tunable knob of Module 3 lives here so that the processing behaviour can be
changed without touching the pipeline code.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Application -------------------------------------------------------
    app_name: str = "niti-setu-civic-data-processing"
    service_name: str = "civic-data-processing"
    version: str = "0.1.0"
    environment: str = "development"
    api_prefix: str = "/api/v1"

    # --- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    # --- Database ----------------------------------------------------------
    database_url: str = "sqlite:///./civic_processing.db"
    auto_create_schema: bool = True
    postgis_enabled: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_pre_ping: bool = True

    # --- Event publishing (Module 4 integration) ---------------------------
    event_publisher: str = "redis"  # redis | noop
    redis_url: str = "redis://localhost:6379/0"
    event_channel: str = "civic.records.processed"
    event_publish_max_retries: int = 3
    event_retry_backoff_seconds: float = 0.5
    event_outbox_batch_size: int = 100

    # --- Geocoding ---------------------------------------------------------
    geocoding_provider: str = "mock"  # mock | nominatim | chain
    geocoding_fallback_provider: str = "mock"
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    geocoding_timeout_seconds: float = 5.0
    geocoding_user_agent: str = "niti-setu-civic-data-processing/0.1"
    geocoding_cache_ttl_seconds: int = 3600
    geocoding_nearest_place_radius_km: float = 60.0

    # --- Deduplication / issue grouping ------------------------------------
    dedup_strategy: str = "rule_based"  # rule_based | embedding (future)
    dedup_radius_meters: float = 1000.0
    dedup_time_window_hours: int = 720  # 30 days
    dedup_description_similarity_threshold: float = 0.30
    dedup_min_match_score: float = 0.55

    # --- Security / limits -------------------------------------------------
    max_request_bytes: int = 262_144
    max_description_length: int = 5_000
    max_text_field_length: int = 1_000
    default_page_size: int = 50
    max_page_size: int = 200

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
