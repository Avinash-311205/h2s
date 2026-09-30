"""Image analysis stage for citizen photo evidence.

Design principle: **report measurements, not guesses.** A civic system that
labels a blurry photo "flooded street" is worse than useless -- it seeds
false reports into the pipeline. So this stage computes objective, auditable
image quality signals (brightness, contrast, sharpness, colour cast, aspect)
and only emits a *label* when a real model is installed.

``heuristics`` (default, zero dependencies):
    PIL-only measurements. Runs anywhere, instantly, and always reports
    whether the image is even usable as evidence.

``clip`` (optional):
    Zero-shot labels from openai/clip-vit-base-patch32 against a civic prompt
    set, e.g. ["a flooded street", "a broken road", ...].
"""

from __future__ import annotations

import io
import threading
from typing import Any, Optional

from app.core.config import settings
from app.core.enums import StageStatus
from app.core.logging import get_logger

logger = get_logger(__name__)

_clip_lock = threading.Lock()
_clip_cache: dict[str, Any] = {}

# Zero-shot prompt set for CLIP: the civic conditions a citizen realistically
# photographs. Kept short because each prompt costs inference time.
CLIP_PROMPTS = (
    "a photo of a flooded street with waterlogging",
    "a photo of a pothole or damaged road surface",
    "a photo of a broken water pipeline or leaking tap",
    "a photo of garbage or a waste dump",
    "a photo of a sewage drain overflowing",
    "a photo of a dark street with no working street lights",
    "a photo of a damaged or unsafe bridge",
    "a photo of a non-functional electric pole or transformer",
    "a photo of a government school or hospital building",
)


class ImageAnalysis:
    """Outcome of the image stage."""

    def __init__(
        self,
        width: int,
        height: int,
        format_name: str,
        brightness: float,
        contrast: float,
        sharpness: float,
        color_cast: str,
        quality: str,
        is_blurry: bool,
        is_dark: bool,
        labels: list[dict],
        provider: str,
        status: str,
        detail: Optional[str] = None,
    ) -> None:
        self.width = width
        self.height = height
        self.format_name = format_name
        self.brightness = brightness
        self.contrast = contrast
        self.sharpness = sharpness
        self.color_cast = color_cast
        self.quality = quality
        self.is_blurry = is_blurry
        self.is_dark = is_dark
        self.labels = labels
        self.provider = provider
        self.status = status
        self.detail = detail

    def as_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "format": self.format_name,
            "brightness": round(self.brightness, 2),
            "contrast": round(self.contrast, 2),
            "sharpness": round(self.sharpness, 2),
            "color_cast": self.color_cast,
            "quality": self.quality,
            "is_blurry": self.is_blurry,
            "is_dark": self.is_dark,
            "labels": self.labels,
            "provider": self.provider,
            "status": self.status,
            "detail": self.detail,
        }


def is_available() -> bool:
    """True when Pillow (measurements) can run."""
    try:
        import PIL  # noqa: F401
    except Exception:
        return False
    return True


def has_clip() -> bool:
    """True when an open-weight CLIP model can provide real labels."""
    if settings.image_provider == "heuristics":
        return False
    try:  # pragma: no cover - optional dependency
        import transformers  # noqa: F401
    except Exception:
        return False
    return True


def _load_clip():
    """Load (and cache) the CLIP zero-shot pipeline."""
    with _clip_lock:
        if "pipeline" in _clip_cache:
            return _clip_cache["pipeline"]
        try:  # pragma: no cover - optional heavy path
            from transformers import pipeline as hf_pipeline

            logger.info("clip_model_loading", extra={"model": settings.clip_model})
            _clip_cache["pipeline"] = hf_pipeline("zero-shot-image-classification", model=settings.clip_model)
            return _clip_cache["pipeline"]
        except Exception as exc:
            logger.warning("clip_model_unavailable", extra={"error": str(exc)})
            _clip_cache["pipeline"] = None
            return None


def analyze(image_bytes: bytes) -> ImageAnalysis:
    """Compute quality signals (and labels when a model is available).

    Args:
        image_bytes: raw image payload from the citizen.

    Returns:
        An :class:`ImageAnalysis`. Never raises for a bad image -- it reports
        ``FAILED`` with the reason so the request still succeeds on text.
    """
    if not image_bytes:
        return _failed("empty image payload")
    if len(image_bytes) > settings.max_image_bytes:
        return _failed(f"image exceeds {settings.max_image_bytes} byte limit")

    try:
        from PIL import Image, ImageStat
    except Exception:
        return _failed("Pillow is required for image analysis (pip install pillow)")

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        original_format = (image.format or "UNKNOWN").upper()

        # Downscale large phone photos: analysis quality does not improve past
        # this size and it keeps per-request cost bounded.
        if max(image.size) > settings.image_max_dimension:
            image.thumbnail((settings.image_max_dimension, settings.image_max_dimension))

        grayscale = image.convert("L")
        stat = ImageStat.Stat(grayscale)
        brightness = stat.mean[0]
        contrast = stat.stddev[0]
        sharpness = _sharpness(image)

        is_dark = brightness < settings.image_dark_threshold
        is_blurry = sharpness < settings.image_blur_threshold

        labels: list[dict] = []
        provider = "heuristics"
        if has_clip():
            classifier = _load_clip()
            if classifier is not None:
                try:  # pragma: no cover - optional heavy path
                    raw = classifier(image.convert("RGB"), candidate_labels=list(CLIP_PROMPTS))
                    labels = [
                        {"label": item["label"], "score": round(float(item["score"]), 4)}
                        for item in raw
                    ]
                    provider = "clip"
                except Exception as exc:
                    logger.warning("clip_inference_failed", extra={"error": str(exc)})

        return ImageAnalysis(
            width=image.width,
            height=image.height,
            format_name=original_format,
            brightness=brightness,
            contrast=contrast,
            sharpness=sharpness,
            color_cast=_color_cast(image),
            quality=_quality_band(brightness, is_blurry, is_dark),
            is_blurry=is_blurry,
            is_dark=is_dark,
            labels=labels,
            provider=provider,
            # FALLBACK rather than OK: we produced measurements, not labels.
            status=StageStatus.OK.value if labels else StageStatus.FALLBACK.value,
            detail=None
            if labels
            else "Measured image quality only; no image model installed for content labels.",
        )
    except Exception as exc:
        logger.warning("image_analysis_failed", extra={"error": str(exc)})
        return _failed(f"unreadable image: {exc}")


def _sharpness(image) -> float:
    """Laplacian-variance sharpness, falling back to an edge-energy proxy.

    OpenCV is used when present (the reference implementation of this metric);
    otherwise PIL edge gradients give a comparable, if coarser, signal.
    """
    try:
        import cv2
        import numpy as np

        array = np.array(image.convert("L"))
        return float(cv2.Laplacian(array, cv2.CV_64F).var())
    except Exception:
        pass

    # Pure-PIL fallback: mean absolute difference between a pixel and its
    # right neighbour. Higher = more edges = sharper.
    try:
        grayscale = image.convert("L")
        width, height = grayscale.size
        pixels = list(grayscale.getdata())
        row = width
        total = 0
        count = 0
        for y in range(1, height):
            base = y * row
            for x in range(1, row):
                total += abs(pixels[base + x] - pixels[base + x - 1])
                count += 1
        return (total / count) if count else 0.0
    except Exception:
        return 0.0


def _color_cast(image) -> str:
    """Coarse colour-cast hint from the mean RGB channels.

    Useful signal for civic triage: a blue/grey cast often accompanies water
   logging photos, a yellow cast indoor or low-light shots.
    """
    try:
        from PIL import ImageStat

        means = ImageStat.Stat(image.convert("RGB")).mean
        red, green, blue = means[:3]
        if blue > red + 12 and blue > green + 8:
            return "blue"
        if red > green + 18 and red > blue + 18:
            return "warm"
        if green > red + 8 and green > blue + 8:
            return "green"
        if max(means) - min(means) < 12:
            return "grey"
        return "mixed"
    except Exception:
        return "unknown"


def _quality_band(brightness: float, is_blurry: bool, is_dark: bool) -> str:
    """Bucket the measurements into an evidence-usability verdict."""
    if is_blurry:
        return "poor_blurry"
    if is_dark:
        return "poor_dark"
    if brightness < 80 or brightness > 225:
        return "usable_low_light"
    return "usable"


def _failed(detail: str) -> ImageAnalysis:
    return ImageAnalysis(
        width=0,
        height=0,
        format_name="UNKNOWN",
        brightness=0.0,
        contrast=0.0,
        sharpness=0.0,
        color_cast="unknown",
        quality="unavailable",
        is_blurry=False,
        is_dark=False,
        labels=[],
        provider="none",
        status=StageStatus.FAILED.value,
        detail=detail,
    )


def capabilities() -> dict:
    """Describe the image stage for ``GET /capabilities``."""
    return {
        "stage": "image_analysis",
        # Measurements are always available, so an image request still succeeds
        # without PIL or CLIP installed.
        "available": is_available(),
        "method": "heuristic_measurements" if not has_clip() else "clip_labels_and_measurements",
        "configured_provider": settings.image_provider,
        "model": settings.clip_model if has_clip() else None,
        "detail": {
            "labels_available": has_clip(),
            "blur_threshold": settings.image_blur_threshold,
            "clip_prompts": len(CLIP_PROMPTS),
        },
    }
