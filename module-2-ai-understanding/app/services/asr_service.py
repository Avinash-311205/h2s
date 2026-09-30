"""Speech-to-text stage.

Provider strategy (all offline, no API keys):

* ``faster_whisper`` -- local open-weight Whisper. Only used when the package
  is importable *and* a model can be loaded. Whisper handles 90+ Indic
  languages, which is why it is the recommended provider.
* ``disabled`` -- no ASR. The stage reports ``SKIPPED`` and the orchestrator
  falls back to any text the citizen typed.

The service must never crash the request: a missing dependency or an
undecodable file downgrades to a reported failure, not a 500.
"""

from __future__ import annotations

import io
import threading
from typing import Any, Optional

from app.core.config import settings
from app.core.enums import StageStatus
from app.core.logging import get_logger

logger = get_logger(__name__)

# One Whisper model per process: loading is expensive and thread-unsafe.
_model_lock = threading.Lock()
_model_cache: dict[str, Any] = {}


class ASRResult:
    """Outcome of the speech-to-text stage."""

    def __init__(
        self,
        text: str,
        language: Optional[str],
        confidence: float,
        duration_seconds: Optional[float],
        provider: str,
        status: str,
        detail: Optional[str] = None,
    ) -> None:
        self.text = text
        self.language = language
        self.confidence = confidence
        self.duration_seconds = duration_seconds
        self.provider = provider
        self.status = status
        self.detail = detail

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "confidence": self.confidence,
            "duration_seconds": self.duration_seconds,
            "provider": self.provider,
            "status": self.status,
            "detail": self.detail,
        }


def is_available() -> bool:
    """True when a real ASR provider can be used in this process."""
    if settings.asr_provider == "disabled":
        return False
    try:  # pragma: no cover - depends on optional dependency
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def _load_model():
    """Load (and cache) the Whisper model. Returns ``None`` when unavailable."""
    with _model_lock:
        cached = _model_cache.get("model")
        if cached is not None:
            return cached
        try:  # pragma: no cover - optional heavy path
            from faster_whisper import WhisperModel

            logger.info(
                "asr_model_loading",
                extra={"model": settings.whisper_model_size, "device": settings.whisper_device},
            )
            model = WhisperModel(
                settings.whisper_model_size,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
            _model_cache["model"] = model
            return model
        except Exception as exc:
            logger.warning("asr_model_unavailable", extra={"error": str(exc)})
            return None


def transcribe(audio_bytes: bytes, hint_language: Optional[str] = None) -> ASRResult:
    """Transcribe citizen audio to text.

    Args:
        audio_bytes: raw audio payload uploaded by the citizen.
        hint_language: optional ISO code to bias Whisper's language detection.

    Returns:
        An :class:`ASRResult`. On any problem the status becomes ``SKIPPED``
        or ``FAILED`` and the caller decides how to proceed.
    """
    if not audio_bytes:
        return ASRResult(
            text="",
            language=None,
            confidence=0.0,
            duration_seconds=None,
            provider="none",
            status=StageStatus.SKIPPED.value,
            detail="empty audio payload",
        )

    if len(audio_bytes) > settings.max_audio_bytes:
        return ASRResult(
            text="",
            language=None,
            confidence=0.0,
            duration_seconds=None,
            provider="none",
            status=StageStatus.FAILED.value,
            detail=f"audio exceeds {settings.max_audio_bytes} byte limit",
        )

    model = _load_model()
    if model is None:
        return ASRResult(
            text="",
            language=None,
            confidence=0.0,
            duration_seconds=None,
            provider="none",
            status=StageStatus.SKIPPED.value,
            detail=(
                "ASR provider unavailable. Install faster-whisper "
                "(pip install faster-whisper) or submit the request as text."
            ),
        )

    # --- real transcription (optional dependency present) -------------------
    try:  # pragma: no cover - optional heavy path
        segments, info = model.transcribe(
            io.BytesIO(audio_bytes),
            language=canonical_whisper_language(hint_language),
            beam_size=5,
            vad_filter=True,  # skip silence, big speed-up on phone recordings
        )
        chunks = [segment.text for segment in segments]
        text = "".join(chunks).strip()
        duration = getattr(info, "duration", None)
        # Whisper exposes avg_logprob; map it to a 0-1 confidence.
        confidence = _logprob_to_confidence(getattr(info, "avg_logprob", None))
        return ASRResult(
            text=text,
            language=getattr(info, "language", None),
            confidence=confidence,
            duration_seconds=round(duration, 2) if duration else None,
            provider="faster_whisper",
            status=StageStatus.OK.value if text else StageStatus.FAILED.value,
        )
    except Exception as exc:
        logger.warning("asr_transcription_failed", extra={"error": str(exc)})
        return ASRResult(
            text="",
            language=None,
            confidence=0.0,
            duration_seconds=None,
            provider="faster_whisper",
            status=StageStatus.FAILED.value,
            detail=str(exc),
        )


def _logprob_to_confidence(avg_logprob: Optional[float]) -> float:
    """Convert Whisper's average token log-probability to a 0-1 confidence."""
    if avg_logprob is None:
        return 0.0
    import math

    return round(max(0.0, min(1.0, math.exp(avg_logprob))), 4)


def canonical_whisper_language(hint: Optional[str]) -> Optional[str]:
    """Map our language codes to Whisper's expected ISO-639-1 codes."""
    if not hint:
        return None
    mapping = {
        "en": "en",
        "ta": "ta",
        "hi": "hi",
        "te": "te",
        "bn": "bn",
        "kn": "kn",
        "ml": "ml",
        "mr": "mr",
    }
    # ``romanised`` and ``und`` give Whisper no useful prior.
    return mapping.get(hint)


def capabilities() -> dict:
    """Report ASR availability for ``GET /capabilities``."""
    return {
        "stage": "asr",
        "available": is_available(),
        "method": "openai_whisper_via_faster_whisper" if is_available() else "unavailable",
        "configured_provider": settings.asr_provider,
        "model": settings.whisper_model_size if is_available() else None,
        "detail": {
            "device": settings.whisper_device,
            "max_duration_seconds": settings.asr_max_duration_seconds,
        },
    }
