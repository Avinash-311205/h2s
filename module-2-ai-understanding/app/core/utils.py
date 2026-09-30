"""Small generic helpers shared by the understanding stages."""

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


def normalize_text(value: str) -> str:
    """Canonicalize citizen text without changing its meaning.

    NFKC folding, typographic punctuation -> ASCII, zero-width/control
    characters removed, whitespace collapsed. Applied before *every* stage so
    the lexicons and regexes all see the same canonical form.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.translate(_TRANSLATION_TABLE)
    text = _CONTROL_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def tokenize(value: str) -> list[str]:
    """Casefold and split text into comparison tokens.

    Tokenization keeps Unicode letters (so Tamil / Devanagari / Telugu words
    survive intact) and only splits on non-alphanumeric characters.
    """
    if not value:
        return []
    folded = unicodedata.normalize("NFKC", value).casefold()
    # Keep word characters plus any non-ASCII letter (Indic scripts).
    return [tok for tok in re.split(r"[^\w\u0080-\uffff]+", folded) if tok]


def contains_phrase(haystack_tokens: list[str], phrase: str) -> bool:
    """True when *phrase* appears as a contiguous token run in the tokens.

    Matching on token sequences (rather than substring search) prevents
    "water" from firing inside "underwater" and makes the lexicons
    language-agnostic, because Indic scripts do not use spaces between
    inflected forms in the same way Latin scripts do.
    """
    phrase_tokens = tokenize(phrase)
    if not phrase_tokens:
        return False
    n = len(phrase_tokens)
    for i in range(len(haystack_tokens) - n + 1):
        if haystack_tokens[i : i + n] == phrase_tokens:
            return True
    return False


def sha256_hex(payload: Any) -> str:
    """Stable hash of an arbitrary JSON-serializable payload."""
    if not isinstance(payload, str):
        payload = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    """Short, sortable-enough unique identifier with a readable prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def truncate(value: Optional[str], max_length: int) -> tuple[Optional[str], bool]:
    """Return ``(value, was_truncated)``."""
    if value is None:
        return None, False
    if max_length <= 0 or len(value) <= max_length:
        return value, False
    return value[:max_length], True


def clamp(value: float, low: float, high: float) -> float:
    """Clip *value* into ``[low, high]``."""
    return max(low, min(high, value))


def round_or_none(value: Optional[float], digits: int = 4) -> Optional[float]:
    """Round a float for JSON output, preserving ``None``."""
    if value is None:
        return None
    return round(float(value), digits)
