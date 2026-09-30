"""Settings for Module 5 - Civic Intelligence.

The module reads the mesh's snapshots rather than owning them, so the upstream
location is configuration too. Pointing ``MESH_DATABASE_URL`` at Module 4's file
is enough to sync; everything else has a working default.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Module configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "civic-intelligence"
    version: str = "0.1.0"
    environment: str = "local"
    debug: bool = False

    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8005

    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "sqlite:///./civic_intelligence.db"
    auto_create_schema: bool = True
    db_pool_pre_ping: bool = True

    # --- Upstream mesh ------------------------------------------------------
    # Module 4's database. Only read: Module 5 keeps its own snapshots so it can
    # compute trends over consecutive syncs without depending on mesh uptime.
    mesh_database_url: str = "sqlite:///../module-4-national-data-mesh/national_data_mesh.db"
    mesh_timeout_seconds: float = 5.0

    # --- Hotspots -----------------------------------------------------------
    # Wards within this distance of each other are considered neighbours and can
    # join the same hotspot cluster.
    cluster_radius_m: float = 12_000.0
    # Complaint intensity is reported per this many residents, so wards of
    # different sizes can be compared.
    population_per_unit: int = 1_000
    # A ward is a hotspot at or above this many standard deviations above the
    # city-wide mean intensity.
    hotspot_z_threshold: float = 1.0

    # --- Trends -------------------------------------------------------------
    # Windows are ordered shortest first; each trend is computed per window.
    trend_windows_days: list[int] = [30, 90]
    # Percentage move between the first and last window that counts as a real
    # change rather than noise.
    trend_change_threshold_pct: float = 10.0

    # --- Emerging risks -----------------------------------------------------
    # A recent window this many times the previous one is a spike.
    spike_ratio: float = 2.0
    # ...but only if it is also at least this many complaints in absolute terms,
    # so a jump from 1 to 3 does not raise an alarm.
    spike_min_complaints: int = 10
    # Projects past this share of their window with under this much spend.
    stall_elapsed_fraction: float = 0.5
    stall_spend_ratio: float = 0.35


settings = Settings()