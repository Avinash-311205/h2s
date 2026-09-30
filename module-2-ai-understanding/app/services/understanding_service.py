"""Understanding orchestrator: the Module 2 pipeline.

Runs the stages in dependency order and records the outcome of each one::

    input (text | audio | image)
        -> language detection
        -> ASR              (audio only; adds text when available)
        -> translation      (-> English for the classifiers)
        -> NER              (entities from the English text)
        -> classification   (category + sub-category)
        -> severity         (1-5)
        -> image analysis   (image only)

Two invariants make the output trustworthy downstream:

1. **Nothing is fabricated.** Every stage reports ``OK`` / ``FALLBACK`` /
   ``SKIPPED`` / ``FAILED`` and the record keeps the raw input, so Module 3 can
   always tell an inferred field from an observed one.
2. **Text always wins over audio/image.** If a citizen sends both a voice note
   and typed text, the typed text is authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.enums import (
    MediaType,
    StageStatus,
    UnderstandingStage,
    UnderstandingStatus,
)
from app.core.logging import bind_request_context, get_logger, set_stage
from app.services import (
    asr_service,
    classification_service,
    image_service,
    language_service,
    ner_service,
    translation_service,
)

logger = get_logger(__name__)


@dataclass
class Understanding:
    """The full structured result for one citizen request."""

    request_id: str
    media_type: str
    # --- observed / inferred text -----------------------------------------
    detected_language: str = "und"
    language_confidence: float = 0.0
    raw_text: str = ""
    transcript: Optional[str] = None
    original_text: str = ""
    english_text: str = ""
    translation_provider: str = "passthrough"
    translation_is_gloss: bool = False
    # --- classification ----------------------------------------------------
    category: str = "UNKNOWN"
    sub_category: str = "UNCLASSIFIED"
    category_confidence: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    # --- severity ----------------------------------------------------------
    severity: int = 1
    severity_band: str = "LOW"
    severity_confidence: float = 0.0
    # --- entities / media --------------------------------------------------
    entities: list[dict] = field(default_factory=list)
    image_analysis: Optional[dict] = None
    # --- audit -------------------------------------------------------------
    stage_status: dict[str, str] = field(default_factory=dict)
    status: str = UnderstandingStatus.UNDERSTOOD.value
    raw_payload: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "media_type": self.media_type,
            "detected_language": self.detected_language,
            "language_confidence": round(self.language_confidence, 4),
            "original_text": self.original_text,
            "transcript": self.transcript,
            "english_text": self.english_text,
            "translation_provider": self.translation_provider,
            "translation_is_gloss": self.translation_is_gloss,
            "category": self.category,
            "sub_category": self.sub_category,
            "category_confidence": round(self.category_confidence, 4),
            "matched_keywords": self.matched_keywords,
            "severity": self.severity,
            "severity_band": self.severity_band,
            "severity_confidence": round(self.severity_confidence, 4),
            "entities": self.entities,
            "image_analysis": self.image_analysis,
            "stage_status": self.stage_status,
            "status": self.status,
            "warnings": self.warnings,
        }


def understand(
    request_id: str,
    text: Optional[str] = None,
    audio_bytes: Optional[bytes] = None,
    image_bytes: Optional[bytes] = None,
    hint_language: Optional[str] = None,
    hint_latitude: Optional[float] = None,
    hint_longitude: Optional[float] = None,
) -> Understanding:
    """Run the full understanding pipeline for one citizen request.

    Args:
        request_id: upstream (Module 1) request identifier.
        text: citizen text, if submitted.
        audio_bytes: voice note payload, if submitted.
        image_bytes: photo payload, if submitted.
        hint_language: caller-supplied language hint (e.g. from the channel).
        hint_latitude / hint_longitude: GPS from the handset, kept in the audit
            payload for Module 3's geocoder (never overwritten by inference).

    Returns:
        A populated :class:`Understanding`.
    """
    bind_request_context(request_id)
    has_audio = bool(audio_bytes)
    has_image = bool(image_bytes)
    has_text = bool(text and text.strip())

    if not (has_text or has_audio or has_image):
        raise ValueError("At least one of text, audio or image must be provided.")

    media_type = _media_type(has_text, has_audio, has_image)
    result = Understanding(
        request_id=request_id,
        media_type=media_type,
        raw_payload={
            "has_text": has_text,
            "has_audio": has_audio,
            "has_image": has_image,
            "hint_language": hint_language,
            "hint_latitude": hint_latitude,
            "hint_longitude": hint_longitude,
        },
        # Pre-seed every stage as SKIPPED. Stages that do not apply to this
        # media type stay SKIPPED in the audit trail instead of vanishing from
        # the map, so a reader can always see which stages were considered.
        stage_status={stage.value: StageStatus.SKIPPED.value for stage in UnderstandingStage},
    )

    # ---------------- Stage 1: language detection --------------------------
    set_stage(UnderstandingStage.LANGUAGE_DETECTION.value)
    source_text = text.strip() if has_text else ""

    if has_audio and not has_text:
        # We need *some* text before we can detect a language, so run ASR first
        # when the citizen only sent audio. This re-ordering is why the stage
        # status map exists: it records the true execution order.
        set_stage(UnderstandingStage.ASR.value)
        asr = asr_service.transcribe(audio_bytes, hint_language=hint_language)
        result.stage_status[UnderstandingStage.ASR.value] = asr.status
        result.transcript = asr.text or None
        if asr.detail and asr.status in {StageStatus.SKIPPED.value, StageStatus.FAILED.value}:
            result.warnings.append(f"asr: {asr.detail}")
        if asr.text:
            source_text = asr.text
        elif asr.language:
            result.detected_language = asr.language

    set_stage(UnderstandingStage.LANGUAGE_DETECTION.value)
    if source_text:
        detection = language_service.detect_language(source_text)
        result.detected_language = detection["language"]
        result.language_confidence = detection["confidence"]
        result.stage_status[UnderstandingStage.LANGUAGE_DETECTION.value] = (
            language_service.stage_status_for(detection)
        )
    else:
        result.stage_status[UnderstandingStage.LANGUAGE_DETECTION.value] = StageStatus.SKIPPED.value
        result.warnings.append("No text available for language detection.")

    # A caller hint never overrides a strong script-based detection.
    if hint_language and result.language_confidence < 0.75:
        result.detected_language = language_service.canonical_language_code(hint_language)

    result.original_text = source_text

    # ---------------- Stage 2: translation ---------------------------------
    set_stage(UnderstandingStage.TRANSLATION.value)
    if source_text:
        translation = translation_service.translate(
            source_text, result.detected_language
        )
        result.english_text = translation.text
        result.translation_provider = translation.provider
        result.translation_is_gloss = translation.is_gloss
        result.stage_status[UnderstandingStage.TRANSLATION.value] = translation.status
        if translation.detail:
            result.warnings.append(f"translation: {translation.detail}")
    else:
        result.english_text = ""
        result.stage_status[UnderstandingStage.TRANSLATION.value] = StageStatus.SKIPPED.value

    # ---------------- Stage 3: NER -----------------------------------------
    set_stage(UnderstandingStage.NER.value)
    # Always run NER on both the original and the English text: a pincode or
    # phone number survives translation, and a landmark in the source language
    # is only findable there.
    ner_result = ner_service.extract_entities(result.english_text or source_text)
    source_ner = (
        ner_service.extract_entities(result.original_text)
        if result.original_text and result.original_text != result.english_text
        else None
    )
    merged_entities = list(ner_result.entities)
    if source_ner:
        known = {(entity.type, entity.text) for entity in merged_entities}
        merged_entities.extend(
            entity
            for entity in source_ner.entities
            if (entity.type, entity.text) not in known
        )
    result.entities = [entity.as_dict() for entity in merged_entities]
    result.stage_status[UnderstandingStage.NER.value] = ner_result.status

    # ---------------- Stage 4: classification ------------------------------
    set_stage(UnderstandingStage.CLASSIFICATION.value)
    # Classify on English text first, then fall back to the original text: a
    # gloss may miss a keyword that is obvious in the source language.
    classification_input = result.english_text or result.original_text
    prediction = classification_service.classify_category(classification_input)
    if prediction.category == "UNKNOWN" and result.original_text and result.original_text != classification_input:
        prediction = classification_service.classify_category(result.original_text)

    result.category = prediction.category
    result.sub_category = prediction.sub_category
    result.category_confidence = prediction.confidence
    result.matched_keywords = prediction.matched_keywords
    result.stage_status[UnderstandingStage.CLASSIFICATION.value] = (
        StageStatus.OK.value if prediction.category != "UNKNOWN" else StageStatus.FALLBACK.value
    )
    if prediction.category == "UNKNOWN":
        result.warnings.append("Category could not be determined; routed for review.")

    # ---------------- Stage 5: severity ------------------------------------
    set_stage(UnderstandingStage.SEVERITY.value)
    max_duration = _max_duration_days(result.entities)

    # Score on the English text *and* on the original: the offline gloss only
    # covers a handful of civic terms, so a complaint can contain a strong
    # severity keyword in Tamil that never reaches the English lexicons.
    severity = classification_service.score_severity(
        classification_input, prediction, max_duration_days=max_duration
    )
    if result.original_text and result.original_text != classification_input:
        source_severity = classification_service.score_severity(
            result.original_text, prediction, max_duration_days=max_duration
        )
        if source_severity.severity > severity.severity:
            severity = source_severity

    result.severity = severity.severity
    result.severity_band = severity.band
    result.severity_confidence = severity.confidence
    result.stage_status[UnderstandingStage.SEVERITY.value] = StageStatus.OK.value

    # ---------------- Stage 6: image analysis ------------------------------
    if has_image:
        set_stage(UnderstandingStage.IMAGE_ANALYSIS.value)
        analysis = image_service.analyze(image_bytes)
        result.image_analysis = analysis.as_dict()
        result.stage_status[UnderstandingStage.IMAGE_ANALYSIS.value] = analysis.status
        if analysis.status == StageStatus.FAILED.value and analysis.detail:
            result.warnings.append(f"image: {analysis.detail}")
    else:
        result.stage_status[UnderstandingStage.IMAGE_ANALYSIS.value] = StageStatus.SKIPPED.value

    # ---------------- Overall status ---------------------------------------
    result.status = _overall_status(result)
    logger.info(
        "understanding_complete",
        extra={
            "category": result.category,
            "severity": result.severity,
            "language": result.detected_language,
            "status": result.status,
        },
    )
    return result


def _media_type(has_text: bool, has_audio: bool, has_image: bool) -> str:
    if sum((has_text, has_audio, has_image)) > 1:
        return MediaType.MULTIMODAL.value
    if has_audio:
        return MediaType.AUDIO.value
    if has_image:
        return MediaType.IMAGE.value
    return MediaType.TEXT.value


def _max_duration_days(entities: list[dict]) -> Optional[int]:
    """Longest DURATION entity in days, used for the persistence escalation."""
    per_day = {
        "day": 1, "days": 1, "night": 1, "nights": 1, "din": 1, "दिन": 1, "दिनों": 1,
        "రಹజు": 1, "రಹజులు": 1,
        "வ஛ு": 1, "நாள்": 1, "நாட்கள்": 1,
    }
    per_week = {
        "week": 7, "weeks": 7, "vaaram": 7,
        "வாரம்": 7, "வாரங்கள்": 7,
        "सप्ताह": 7, "वार": 7, "వారం": 7,
    }
    per_month = {
        "month": 30, "months": 30, "maas": 30,
        "மாதம்": 30, "மாதங்கள்": 30,
        "महୀना": 30, "నెల": 30, "నెలల": 30,
    }

    best: Optional[int] = None
    for entity in entities:
        if entity.get("type") != "DURATION":
            continue
        attributes = entity.get("attributes") or {}
        amount = attributes.get("amount")
        unit = attributes.get("unit")
        if amount is None or unit is None:
            continue
        multiplier = per_day.get(unit) or per_week.get(unit) or per_month.get(unit)
        if multiplier is None:
            continue
        days = int(amount) * multiplier
        if best is None or days > best:
            best = days
    return best


def _overall_status(result: Understanding) -> str:
    """UNDERSTOOD when every attempted stage succeeded, else PARTIAL/FAILED."""
    values = set(result.stage_status.values())
    if values <= {StageStatus.OK.value, StageStatus.SKIPPED.value}:
        return UnderstandingStatus.UNDERSTOOD.value
    if StageStatus.FAILED.value in values and not result.english_text and not result.original_text:
        return UnderstandingStatus.FAILED.value
    return UnderstandingStatus.PARTIAL.value
