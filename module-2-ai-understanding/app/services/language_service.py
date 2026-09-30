"""Language identification by Unicode script and stopword evidence.

Why script detection first: Indian citizen input is dominated by a handful of
scripts (Devanagari for Hindi, Tamil, Telugu, Bengali, Kannada, Malayalam), and
a script maps to essentially one dominant language in this domain. Combining
script ranges with a small stopword hit-rate disambiguates the Latin script
(which covers English plus romanised Hindi/Tamil) without needing any
third-party language-detection model.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from app.core.enums import UnderstandingStage
from app.core.logging import get_logger
from app.core.utils import normalize_text, tokenize

logger = get_logger(__name__)

_LEXICON_PATH = Path(__file__).resolve().parent.parent / "resources" / "lexicons.json"

# Unicode script ranges -> ISO 639-1. Ranges are (start, end) codepoint pairs.
_SCRIPT_RANGES: tuple[tuple[str, tuple[int, int]], ...] = (
    ("ta", (0x0B80, 0x0BFF)),  # Tamil
    ("te", (0x0C00, 0x0C7F)),  # Telugu
    ("kn", (0x0C80, 0x0CFF)),  # Kannada
    ("ml", (0x0D00, 0x0D7F)),  # Malayalam
    ("bn", (0x0980, 0x09FF)),  # Bengali
    ("hi", (0x0900, 0x097F)),  # Devanagari (Hindi / Marathi)
    ("gu", (0x0A80, 0x0AFF)),  # Gujarati
    ("pa", (0x0A00, 0x0A7F)),  # Gurmukhi (Punjabi)
    ("or", (0x0B00, 0x0B7F)),  # Odia
    ("mr", (0x0900, 0x097F)),  # Marathi shares Devanagari; resolved by lexicon
)

# Marathi-specific stopwords: Devanagari alone cannot separate hi from mr.
_MARATHI_MARKERS = frozenset(
    {
        "आहे", "आणि", "मध्ये", "साठी", "नाही", "पण", "हा", "ही", "हे", "मला", "माझ्या",
        "आपल्या", "काही", "जास्त", "नाहीत", "केले", "केली", "लागू", "ठिकाण",
    }
)

_HINDI_MARKERS = frozenset(
    {
        "है", "हैं", "और", "में", "के", "की", "का", "नहीं", "पर", "से", "यह", "वह",
        "मुझे", "मेरा", "आप", "बहुत", "कुछ", "नहीं", "गया", "करना", "चाहिए", "जगह",
    }
)

# Romanised (transliterated) civic markers: Latin script + these words is far
# more likely to be romanised Indic than English in a civic-complaint corpus.
_ROMANISED_INDIC_MARKERS = frozenset(
    {
        "hai", "nahi", "nahin", "nahi", "kya", "kyu", "kyun", "hai", "hain", "mein",
        "mere", "mera", "apne", "kripya", "dhanyavaad", "gaon", "nagar", "sarkar",
        "panchayat", "bijli", "paani", "sadak", "ghar", "khidki", "gali",
    }
)

_ENGLISH_MARKERS = frozenset(
    {
        "the", "is", "are", "was", "not", "no", "water", "road", "power", "light",
        "please", "since", "days", "weeks", "broken", "dirty", "leak", "flood",
    }
)

# Confidence assigned to each detection method so the API can show *why* we
# believe a language, and how firmly.
_SCRIPT_ONLY_CONFIDENCE = 0.90
_ROMANISED_CONFIDENCE = 0.70
_ENGLISH_CONFIDENCE = 0.85
_DEFAULT_CONFIDENCE = 0.30


@lru_cache(maxsize=1)
def load_lexicons() -> dict:
    """Load and cache the keyword gazetteer from ``resources/lexicons.json``."""
    with _LEXICON_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _script_of(char: str) -> str | None:
    """Return the language hint for a character's script, if recognised."""
    codepoint = ord(char)
    for language, (start, end) in _SCRIPT_RANGES:
        if start <= codepoint <= end:
            return language
    return None


def _dominant_script(text: str) -> tuple[str | None, int, int]:
    """Return ``(script, hits, total_letters)`` for the dominant script."""
    counts: dict[str, int] = {}
    total = 0
    for char in text:
        # Count letters *and* combining marks. In Indic scripts the vowel sign
        # is a separate codepoint (category Mn), so counting letters alone
        # would under-count Tamil/Telugu/Kannada roughly 2:1 against Latin and
        # flip mixed-script complaints such as "சாலை பழுது pincode 560001".
        if not unicodedata.category(char)[0] in {"L", "M"}:
            continue  # ignore digits, punctuation, symbols
        total += 1
        script = _script_of(char)
        if script:
            counts[script] = counts.get(script, 0) + 1

    if not counts or total == 0:
        return None, 0, total

    # Devanagari is claimed by both hi and mr; treat it as one bucket so the
    # stopword vote below can split the two.
    dominant = max(counts, key=lambda key: counts[key])
    return dominant, counts[dominant], total


def detect_language(text: str) -> dict:
    """Identify the language of citizen text.

    Args:
        text: raw citizen text in any supported script.

    Returns:
        dict with ``language`` (ISO 639-1 or ``"und"``), ``confidence`` (0-1),
        ``method`` (which signal decided it) and ``script`` for auditability.
    """
    canonical = normalize_text(text)
    if not canonical:
        return {
            "language": "und",
            "confidence": 0.0,
            "method": "empty_input",
            "script": None,
        }

    tokens = tokenize(canonical)
    script, script_hits, total_letters = _dominant_script(canonical)

    # --- Indic scripts: a single unambiguous script is enough ---------------
    if script and script not in {"hi", "mr"}:
        # A clear majority of the text in one script => confident.
        share = script_hits / total_letters if total_letters else 0.0
        if share >= 0.5:
            return {
                "language": script,
                "confidence": round(_SCRIPT_ONLY_CONFIDENCE + 0.05 * share, 4),
                "method": "unicode_script",
                "script": script,
            }

    # --- Devanagari: split Hindi vs Marathi using function-word evidence ----
    if script == "hi":
        token_set = set(tokens)
        hi_hits = len(token_set & _HINDI_MARKERS)
        mr_hits = len(token_set & _MARATHI_MARKERS)
        if mr_hits > hi_hits:
            return {
                "language": "mr",
                "confidence": _SCRIPT_ONLY_CONFIDENCE,
                "method": "unicode_script+marathi_markers",
                "script": "mr",
            }
        return {
            "language": "hi",
            "confidence": _SCRIPT_ONLY_CONFIDENCE,
            "method": "unicode_script",
            "script": "hi",
        }

    # --- Latin script: English vs romanised Indic ---------------------------
    token_set = set(tokens)
    english_hits = len(token_set & _ENGLISH_MARKERS)
    indic_hits = len(token_set & _ROMANISED_INDIC_MARKERS)

    if indic_hits > english_hits:
        return {
            "language": "romanised",
            "confidence": _ROMANISED_CONFIDENCE,
            "method": "latin_romanised_markers",
            "script": "latn",
        }
    if english_hits > 0:
        return {
            "language": "en",
            "confidence": _ENGLISH_CONFIDENCE,
            "method": "latin_english_markers",
            "script": "latn",
        }

    return {
        "language": "und",
        "confidence": _DEFAULT_CONFIDENCE,
        "method": "insufficient_evidence",
        "script": script,
    }


def stage_status_for(detection: dict) -> str:
    """Map a detection result to a :class:`StageStatus` value."""
    from app.core.enums import StageStatus

    if detection["language"] == "und":
        return StageStatus.FALLBACK.value
    return StageStatus.OK.value


_LANGUAGE_ALIASES = {
    "en": "en", "eng": "en", "english": "en",
    "ta": "ta", "tam": "ta", "tamil": "ta", "தமிழ்": "ta",
    "hi": "hi", "hin": "hi", "hindi": "hi", "हिन्दी": "hi",
    "te": "te", "tel": "te", "telugu": "te", "తెలుగు": "te",
    "bn": "bn", "ben": "bn", "bengali": "bn", "বাংলা": "bn",
    "kn": "kn", "kan": "kn", "kannada": "kn", "ಕನ್ನಡ": "kn",
    "ml": "ml", "mal": "ml", "malayalam": "ml", "മലയാളം": "ml",
    "mr": "mr", "mar": "mr", "marathi": "mr", "मराठी": "mr",
    "romanised": "romanised", "romanized": "romanised", "transliterated": "romanised",
}

SUPPORTED_LANGUAGES = ("en", "ta", "hi", "te", "bn", "kn", "ml", "mr", "romanised")

_WHITESPACE_TOKEN_RE = re.compile(r"\S+")


def canonical_language_code(value: str | None) -> str:
    """Normalise a caller-supplied language label to a supported code.

    Accepts ISO 639-1 codes, 639-3 codes, English and native names, and
    region/script-qualified tags such as ``en-GB`` or ``zh-Hans-IN``.
    Unrecognised input collapses to ``"und"`` rather than being passed
    through, so a typo can never become a bogus ``detected_language``.
    """
    if not value:
        return "und"
    normalized = value.strip().lower().replace("_", "-")
    if normalized in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[normalized]
    # Drop script/region subtags and retry on the base language subtag.
    base = normalized.split("-", 1)[0]
    if base in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[base]
    return "und"
