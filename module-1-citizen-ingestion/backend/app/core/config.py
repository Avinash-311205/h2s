"""Application configuration for Module 1 - Citizen Ingestion.

Every value is overridable by environment variable (and by a local ``.env``), so
the same image runs against SQLite locally and PostgreSQL in compose.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "niti-setu-citizen-ingestion"
    service_name: str = "citizen-ingestion"
    version: str = "1.0.0"
    environment: str = "development"
    port: int = 8001

    # Anchored to the backend directory rather than "./". A relative SQLite URL
    # resolves against the process working directory, so launching uvicorn from
    # the repo root silently created a second, empty database there.
    database_url: str = f"sqlite:///{BACKEND_DIR / 'citizen_requests.db'}"

    # --- Redis (required: this is the Module 1 -> Module 2 handoff) ---------
    redis_url: str = "redis://localhost:6379/0"
    # Channel Module 2 subscribes to for new submissions.
    event_channel: str = "citizen-requests"
    # Short, so an unreachable Redis fails fast and visibly instead of hanging.
    redis_socket_timeout_seconds: float = 2.0
    # When true, startup refuses to serve if Redis is unreachable. Module 1 is
    # the pipeline origin, so starting without its event bus is a broken start.
    redis_required_at_startup: bool = True

    # --- Object storage (optional: only media uploads need it) -------------
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket_name: str = "citizen-requests"
    # Bounded so an absent object store cannot stall startup or a request.
    minio_timeout_seconds: float = 2.0
    minio_enabled: bool = True

    max_file_size_mb: int = 10

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()