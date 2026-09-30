"""Translation stage: citizen language -> English.

Honesty is a hard requirement here. Module 3's normalizer matches on English
keywords, so a bad translation silently mislabels every downstream record.
This service therefore never fabricates a translation:

* ``huggingface`` -- local NLLB-200 (open weights, no API key) when installed.
* ``glossary`` -- offline fallback that substitutes a curated set of high
  frequency civic terms (water, road, electricity, ...) via
  ``resources/lexicons.json``. The result is explicitly flagged
  ``is_gloss=True`` and the stage status is ``FALLBACK``, so Module 3 knows the
  English text is a partial gloss rather than a real translation.
* ``passthrough`` -- returns the source text unchanged when neither is
  possible, flagged so downstream stages keep the original language.

Every record stores which provider produced the English text.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from app.core.config import settings
from app.core.enums import StageStatus
from app.core.logging import get_logger
from app.core.utils import normalize_text
from app.services.language_service import load_lexicons

logger = get_logger(__name__)

_translator_lock = threading.Lock()
_translator_cache: dict[str, Any] = {}

ENGLISH = "en"


class TranslationResult:
    """Outcome of the translation stage."""

    def __init__(
        self,
        text: str,
        source_language: str,
        target_language: str,
        provider: str,
        status: str,
        is_gloss: bool = False,
        confidence: float = 0.0,
        detail: Optional[str] = None,
    ) -> None:
        self.text = text
        self.source_language = source_language
        self.target_language = target_language
        self.provider = provider
        self.status = status
        self.is_gloss = is_gloss
        self.confidence = confidence
        self.detail = detail

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "provider": self.provider,
            "status": self.status,
            "is_gloss": self.is_gloss,
            "confidence": self.confidence,
            "detail": self.detail,
        }


def is_available() -> bool:
    """True when a real neural translation provider can be loaded."""
    if settings.translation_provider == "passthrough":
        return False
    try:  # pragma: no cover - depends on optional dependency
        import transformers  # noqa: F401
    except Exception:
        return False
    return True


def _load_translator():
    """Load (and cache) the NLLB pipeline. Returns ``None`` when unavailable."""
    with _translator_lock:
        cached = _translator_cache.get("pipeline")
        if cached is not None:
            return cached
        try:  # pragma: no cover - optional heavy path
            from transformers import pipeline as hf_pipeline

            logger.info("translation_model_loading", extra={"model": settings.translation_model})
            translator = hf_pipeline("translation", model=settings.translation_model)
            _translator_cache["pipeline"] = translator
            return translator
        except Exception as exc:
            logger.warning("translation_model_unavailable", extra={"error": str(exc)})
            return None


def translate(
    text: str, source_language: str, target_language: str = ENGLISH
) -> TranslationResult:
    """Translate citizen text into English for downstream normalization.

    Args:
        text: canonicalized source text.
        source_language: ISO 639-1 code from the language stage.
        target_language: always ``en`` for the civic pipeline.

    Returns:
        A :class:`TranslationResult` describing exactly how the English text
        was produced.
    """
    canonical = normalize_text(text)

    # Nothing to do: already English (or undetectable but Latin script).
    if source_language in {ENGLISH, "romanised", "und"}:
        return TranslationResult(
            text=canonical,
            source_language=source_language,
            target_language=target_language,
            provider="passthrough",
            status=StageStatus.SKIPPED.value,
            is_gloss=False,
            confidence=1.0 if source_language == ENGLISH else 0.5,
        )

    # --- real neural translation (optional dependency present) --------------
    translator = _load_translator()
    if translator is not None:
        try:  # pragma: no cover - optional heavy path
            nllb_code = _nllb_language_code(source_language)
            output = translator(
                canonical,
                src_lang=nllb_code,
                tgt_lang="eng_Latn",
                max_length=512,
            )
            translated = output[0]["translated_text"].strip()
            return TranslationResult(
                text=translated,
                source_language=source_language,
                target_language=target_language,
                provider="huggingface",
                status=StageStatus.OK.value,
                is_gloss=False,
                confidence=0.90,
            )
        except Exception as exc:
            logger.warning("translation_failed", extra={"error": str(exc)})

    # --- offline civic glossary fallback ------------------------------------
    if settings.use_civic_glossary:
        glossed, hits = _apply_civic_glossary(canonical, source_language)
        if hits:
            return TranslationResult(
                text=glossed,
                source_language=source_language,
                target_language=target_language,
                provider="civic_glossary",
                status=StageStatus.FALLBACK.value,
                is_gloss=True,
                # Partial coverage: a handful of terms does not deserve the
                # confidence of a real translation.
                confidence=round(min(0.60, 0.2 + 0.08 * hits), 4),
                detail=(
                    f"Partial offline gloss ({hits} civic term(s) substituted). "
                    "Install transformers for full NLLB translation."
                ),
            )

    return TranslationResult(
        text=canonical,
        source_language=source_language,
        target_language=target_language,
        provider="passthrough",
        status=StageStatus.FAILED.value,
        is_gloss=False,
        confidence=0.0,
        detail="No translation provider or glossary match; original text retained.",
    )


def _apply_civic_glossary(text: str, language: str) -> tuple[str, int]:
    """Substitute known civic terms with their English equivalent.

    Longest phrases are replaced first so multi-word entries (e.g. Tamil
    ``ஆரம்ப சிகிச்சை மையம்``) win over their single-word prefixes.
    """
    glossary = load_lexicons().get("civic_glossary", {}).get(language, {})
    if not glossary:
        return text, 0

    hits = 0
    result = text
    for term in sorted(glossary, key=len, reverse=True):
        if term in result:
            occurrences = result.count(term)
            result = result.replace(term, glossary[term])
            hits += occurrences
    return result, hits


def _nllb_language_code(language: str) -> str:
    """Map our ISO 639-1 code to an NLLB FLORES code."""
    mapping = {
        "ta": "tam_Taml",
        "hi": "hin_Deva",
        "te": "tel_Telu",
        "bn": "ben_Beng",
        "kn": "kan_Knda",
        "ml": "mal_Mlym",
        "mr": "mar_Deva",
        "gu": "guj_Gujr",
        "pa": "pan_Guru",
        "or": "ory_Orya",
    }
    return mapping.get(language, "eng_Latn")


def capabilities() -> dict:
    """Report translation availability for ``GET /capabilities``."""
    return {
        "stage": "translation",
        # A glossary fallback still produces usable English, so the stage is
        # available even when the NLLB model is not installed.
        "available": is_available() or settings.use_civic_glossary,
        "method": "nllb_200" if is_available() else "civic_glossary_fallback",
        "configured_provider": settings.translation_provider,
        "model": settings.translation_model if is_available() else None,
        "detail": {
            "glossary_fallback": settings.use_civic_glossary,
            "target_language": settings.translation_target_language,
            "gloss_only": not is_available(),
        },
    }
