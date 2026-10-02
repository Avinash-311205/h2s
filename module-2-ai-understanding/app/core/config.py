"""Application configuration for Module 2 - AI Understanding.

Every tunable knob lives here so the understanding pipeline can be re-tuned
without touching stage code.

The module is designed to run with **no heavy ML dependencies installed**:
each AI capability (ASR, translation, image tagging) is a swappable provider
that falls back to a deterministic, offline implementation. That keeps a
fresh clone runnable in seconds for a demo while still allowing real
open-weight models to be switched on through the environment.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Application -------------------------------------------------------
    app_name: str = "niti-setu-ai-understanding"
    service_name: str = "ai-understanding"
    version: str = "0.1.0"
    environment: str = "development"
    api_prefix: str = "/api/v1"

    # --- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    # --- Database ----------------------------------------------------------
    database_url: str = "sqlite:///./ai_understanding.db"
    auto_create_schema: bool = True
    db_pool_pre_ping: bool = True

    # --- Speech-to-text (ASR) ---------------------------------------------
    # provider: faster_whisper (local open-weight Whisper) | disabled
    # The service starts successfully with `disabled` and reports the missing
    # capability through /capabilities instead of failing to boot.
    asr_provider: str = "auto"  # auto | faster_whisper | disabled
    whisper_model_size: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    asr_max_duration_seconds: int = 120

    # --- Translation -------------------------------------------------------
    # provider: auto (NLLB-200 distilled if available) | passthrough
    # `passthrough` keeps the original text and flags the record so Module 3
    # knows the English text is unverified. Never silently fabricates a
    # translation.
    translation_provider: str = "auto"  # auto | huggingface | passthrough
    translation_model: str = "facebook/nllb-200-distilled-600M"
    translation_target_language: str = "en"
    # Offline civic glossary: maps a handful of high-frequency civic terms to
    # English so the English lexicons still fire on untranslated input.
    use_civic_glossary: bool = True

    # --- Entity + label extraction ----------------------------------------
    # The lexicon/gazetteer NER always runs offline; this only names the
    # provider reported by /capabilities so the stage is auditable.
    ner_provider: str = "lexicon_gazetteer"

    # --- NLP / classification ---------------------------------------------
    # Minimum keyword evidence required before we emit a confident category.
    classification_min_confidence: float = 0.20
    # Severity below this is snapped up to 1 (never return 0 = "unset").
    min_severity: int = 1
    max_severity: int = 5
    # When true, text that looks like a personal/medical matter is routed to
    # the healthcare category even if infrastructure keywords are weak.
    personal_health_routing: bool = True

    # --- Image analysis ----------------------------------------------------
    image_provider: str = "auto"  # auto | clip | heuristics | disabled
    clip_model: str = "openai/clip-vit-base-patch32"
    image_max_dimension: int = 1024
    # Blur threshold on the Laplacian variance (OpenCV convention).
    image_blur_threshold: float = 100.0
    # Below this mean brightness an image is treated as too dark to trust.
    image_dark_threshold: float = 40.0

    # --- Security / limits -------------------------------------------------
    max_upload_bytes: int = 15_728_640  # 15 MB
    max_text_length: int = 5_000
    max_audio_bytes: int = 20_971_520  # 20 MB
    max_image_bytes: int = 15_728_640  # 15 MB
    max_batch_size: int = 50
    default_page_size: int = 50
    max_page_size: int = 200

    # --- HTTP ---------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8002

    # --- Upstream (Module 1) integration ----------------------------------
    # Module 1 mints the request_id and announces it on `ingestion_channel`.
    # The announcement carries the fields needed to start work, and this
    # service fetches the authoritative stored row from Module 1 before
    # understanding it, so the pipeline never trusts an event payload alone.
    ingestion_base_url: str = "http://localhost:8001"
    ingestion_channel: str = "citizen-requests"
    ingestion_event_type: str = "REQUEST_RECEIVED"
    ingestion_fetch_timeout_seconds: float = 5.0

    # --- Downstream (Module 2 -> Module 3) ---------------------------------
    # Module 3 consumes this channel and calls its own /civic/process.
    understanding_channel: str = "civic.understanding.completed"
    understanding_event_type: str = "UNDERSTANDING_COMPLETED"

    # --- Redis --------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout_seconds: float = 2.0
    # When false the service only serves HTTP and does not consume events,
    # which is what the unit tests and single-module demos use.
    pipeline_consumer_enabled: bool = True
    # Replay anything left PENDING/FAILED from a previous Redis outage.
    pipeline_retry_on_startup: bool = True

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
