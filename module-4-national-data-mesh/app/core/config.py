"""Settings for Module 4 - National Data Mesh.

Every setting is environment-driven so the same image runs in a laptop demo
(no external data sources) or a production deployment pointed at real datastores.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Module configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "national-data-mesh"
    version: str = "0.1.0"
    environment: str = "local"
    debug: bool = False

    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8004

    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "sqlite:///./national_data_mesh.db"
    auto_create_schema: bool = True
    db_pool_pre_ping: bool = True

    # --- Upstream ingestion (Module 3 -> Module 4) ---------------------------
    # Redis is how Module 3 delivers civic records. This module is the sink, so
    # a missing Redis degrades the subscriber rather than the service: the mesh
    # stays queryable and the failure shows up in health as a disconnected
    # subscriber.
    redis_url: str = "redis://localhost:6379/0"
    civic_channel: str = "civic.records.processed"
    civic_event_type: str = "CIVIC_RECORD_PROCESSED"
    pipeline_consumer_enabled: bool = True
    redis_socket_timeout_seconds: float = 2.0

    # --- Geography ----------------------------------------------------------
    # Mean earth radius in metres; haversine maths in `geo_service`.
    earth_radius_m: float = 6_371_008.8
    # A citizen further than this from a ward centroid is "unassigned" rather
    # than silently folded into the nearest ward.
    max_assignment_distance_m: float = 25_000.0

    # --- Gap analysis -------------------------------------------------------
    # Weights for the gap score. They sum to 1.0 and are asserted at startup so
    # the score can never be tuned into an invalid range.
    weight_demand: float = 0.40
    weight_absence: float = 0.35
    weight_quality: float = 0.25
    # Population below this per asset unit means the sector is over-provided.
    coverage_target_ratio: float = 1.0
    # Assets older than this are treated as degraded regardless of stated status.
    degradation_age_years: int = 12

    @property
    def gap_weights(self) -> dict[str, float]:
        return {
            "demand": self.weight_demand,
            "absence": self.weight_absence,
            "quality": self.weight_quality,
        }


settings = Settings()