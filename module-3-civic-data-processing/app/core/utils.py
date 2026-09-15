"""Small generic helpers used across the processing pipeline."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NON_KEY_RE = re.compile(r"[^0-9a-z\u0080-\uffff]+")

_TRANSLATION_TABLE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
    }
)


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def to_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime (naive => UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_text(value: str) -> str:
    """Canonicalize citizen text without changing its meaning.

    - Unicode NFKC so that visually identical inputs compare equally
    - typographic punctuation folded to ASCII equivalents
    - zero-width / control characters removed
    - whitespace collapsed and trimmed
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.translate(_TRANSLATION_TABLE)
    text = _CONTROL_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_key(value: str) -> str:
    """Normalize a category/sub-category token for dictionary lookups."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    text = text.translate(_TRANSLATION_TABLE)
    text = _NON_KEY_RE.sub("_", text)
    return text.strip("_")


def normalize_description(value: str) -> str:
    """Aggressive normalization used for description similarity comparisons."""
    text = normalize_text(value).casefold()
    text = re.sub(r"[^\w\s\u0080-\uffff]", " ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def sha256_hex(payload: Any) -> str:
    """Stable hash of an arbitrary JSON-serializable payload."""
    if not isinstance(payload, str):
        payload = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def truncate(value: Optional[str], max_length: int) -> tuple[Optional[str], bool]:
    """Return ``(value, was_truncated)``."""
    if value is None:
        return None, False
    if max_length <= 0 or len(value) <= max_length:
        return value, False
    return value[:max_length], True
