"""Rule-based Named Entity Recognition stage.

Extraction is regex + gazetteer based, which is the right tool for this
problem: civic complaints contain a small, well-known set of entity types
(pincode, phone, ward/landmark mentions, infrastructure assets, durations)
that a general NER model would need a large annotated corpus to learn. Rules
are auditable, instant, dependency-free, and -- importantly for a civic
system -- easy for a judge to inspect.

Every entity carries a confidence and the exact text span, so Module 3 can
geocode from a landmark mention or attach a duration to a severity judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings
from app.core.enums import EntityType, StageStatus
from app.core.logging import get_logger
from app.core.utils import contains_phrase, normalize_text, tokenize
from app.services.language_service import load_lexicons

logger = get_logger(__name__)

# --- Patterns ---------------------------------------------------------------
# Indian pincode: exactly 6 digits, not part of a longer number.
_PINCODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
# Indian mobile / landline: 10-digit mobile starting 6-9, optionally +91.
_PHONE_RE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
# Explicit coordinates, e.g. "12.9716, 77.5946" or "12.9716 77.5946".
_COORDINATE_RE = re.compile(
    r"(-?\d{1,2}\.\d{3,})\s*[,/]?\s*(-?\d{1,3}\.\d{3,})"
)
# Durations: "3 days", "two weeks", "for 12 days", "ஆறு மாதங்கள்".
# Number and unit words cover English plus Tamil, Hindi and Telugu, because
# "how long has this been broken?" is the single most useful signal for
# severity escalation and most citizens will not type digits.
_NUMBER_WORDS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|"
    r"ஒன்று|இரண்டு|மூன்று|நான்கு|ஐந்து|ஆறு|ஏழு|எட்டு|ஒன்பது|பத்து|"
    r"एक|दो|तीन|चार|पांच|छह|सात|आठ|नौ|दस|"
    r"ఒకటి|రెండు|మూడు|నాలుగు|ఐదు|ఆరు|ఏడు|ఎనిమిది|పది"
)
_DURATION_UNITS = (
    r"day|days|week|weeks|month|months|night|nights|"
    r"din|दिन|दिनों|வோஜు|ரోజు|ரోజులు|நாள்|நாட்கள்|"
    r"वार|सप्ताह|வாரம்|வாரங்கள்|வாரங்கள்|మాతம்|மாதங்கள்|महीना|నెల|నెలల"
)
_DURATION_RE = re.compile(
    # Explicit lookarounds rather than \b: Tamil/Hindi/Telugu units end in a
    # combining vowel sign (category Mn), which Python's \w does not consider a
    # word character, so \b can never match after "நாட்கள்" and every Indic
    # duration would be silently missed.
    rf"(?<!\w)(?:for\s+|since\s+)?(\d{{1,3}}|{_NUMBER_WORDS})\s+({_DURATION_UNITS})(?!\w)",
    re.IGNORECASE,
)
# Relative time expressions that imply an ongoing problem.
_SINCE_RE = re.compile(
    r"\b(since|for a long time|for months|for weeks|மாதங்கள்|వారాలుగా|महीनों|दिनों)\b",
    re.IGNORECASE,
)
# Rough date mentions.
_DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"ஜனவரி|ஜனuary)\b",
    re.IGNORECASE,
)

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    # Tamil
    "ஒன்று": 1, "இரண்டு": 2, "மூன்று": 3, "நான்கு": 4, "ஐந்து": 5,
    "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10,
    # Hindi
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5,
    "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    # Telugu
    "ఒకటి": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "ఐదు": 5,
    "ఆరు": 6, "ఏడు": 7, "ఎనిమిది": 8, "తొమ్మిది": 9, "పది": 10,
}

# Gazetteer match confidence. Gazetteer hits are strong evidence (the term list
# is specific), so they score higher than a loose numeric pattern.
_GAZETTEER_CONFIDENCE = 0.85
_PINCODE_CONFIDENCE = 0.97
_PHONE_CONFIDENCE = 0.95
_COORDINATE_CONFIDENCE = 0.99
_DURATION_CONFIDENCE = 0.80
_DATE_CONFIDENCE = 0.70


@dataclass
class Entity:
    """A single extracted entity."""

    type: str
    text: str
    confidence: float
    start: int
    end: int
    attributes: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "start": self.start,
            "end": self.end,
            "attributes": self.attributes,
        }


class NERResult:
    """Outcome of the NER stage."""

    def __init__(self, entities: list[Entity], status: str = StageStatus.OK.value) -> None:
        self.entities = entities
        self.status = status

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "entities": [entity.as_dict() for entity in self.entities],
        }

    def of_type(self, entity_type: str) -> list[Entity]:
        return [entity for entity in self.entities if entity.type == entity_type]

    def first(self, entity_type: str) -> Optional[Entity]:
        found = self.of_type(entity_type)
        return found[0] if found else None


def extract_entities(text: str, language: str = "en") -> NERResult:
    """Run the full NER rule set over canonicalized text.

    Args:
        text: canonicalized citizen text (post-translation when available).
        language: source language code, used to pick the right gazetteer.

    Returns:
        A :class:`NERResult` holding every entity found, ordered by position.
    """
    canonical = normalize_text(text)
    if not canonical:
        return NERResult(entities=[], status=StageStatus.SKIPPED.value)

    entities: list[Entity] = []
    entities.extend(_extract_patterns(canonical))
    entities.extend(_extract_gazetteer(canonical))

    # Deduplicate: the same span can match two rule families (e.g. a landmark
    # that also contains a number). Keep the highest-confidence interpretation.
    entities.sort(key=lambda e: (e.start, -e.confidence))
    deduped: list[Entity] = []
    seen_spans: set[tuple[int, int]] = set()
    for entity in entities:
        span = (entity.start, entity.end)
        if span in seen_spans:
            continue
        seen_spans.add(span)
        deduped.append(entity)

    deduped.sort(key=lambda e: e.start)
    return NERResult(entities=deduped)


def _extract_patterns(text: str) -> list[Entity]:
    """Regex-driven entities: pincode, phone, coordinates, duration, date."""
    entities: list[Entity] = []

    for match in _COORDINATE_RE.finditer(text):
        try:
            lat, lng = float(match.group(1)), float(match.group(2))
        except ValueError:
            continue
        if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
            entities.append(
                Entity(
                    type=EntityType.COORDINATE.value,
                    text=match.group(0),
                    confidence=_COORDINATE_CONFIDENCE,
                    start=match.start(),
                    end=match.end(),
                    attributes={"latitude": lat, "longitude": lng},
                )
            )

    for match in _PHONE_RE.finditer(text):
        entities.append(
            Entity(
                type=EntityType.PHONE.value,
                text=match.group(0),
                confidence=_PHONE_CONFIDENCE,
                start=match.start(),
                end=match.end(),
            )
        )

    for match in _PINCODE_RE.finditer(text):
        entities.append(
            Entity(
                type=EntityType.PINCODE.value,
                text=match.group(0),
                confidence=_PINCODE_CONFIDENCE,
                start=match.start(),
                end=match.end(),
            )
        )

    for match in _DURATION_RE.finditer(text):
        raw_number = match.group(1).lower()
        amount = _WORD_NUMBERS.get(raw_number, None)
        if amount is None:
            try:
                amount = int(raw_number)
            except ValueError:
                continue
        entities.append(
            Entity(
                type=EntityType.DURATION.value,
                text=match.group(0),
                confidence=_DURATION_CONFIDENCE,
                start=match.start(),
                end=match.end(),
                attributes={"amount": amount, "unit": match.group(2).lower()},
            )
        )

    for match in _SINCE_RE.finditer(text):
        entities.append(
            Entity(
                type=EntityType.DURATION.value,
                text=match.group(0),
                confidence=_DURATION_CONFIDENCE,
                start=match.start(),
                end=match.end(),
                attributes={"amount": None, "unit": "relative"},
            )
        )

    for match in _DATE_RE.finditer(text):
        entities.append(
            Entity(
                type=EntityType.DATE.value,
                text=match.group(0),
                confidence=_DATE_CONFIDENCE,
                start=match.start(),
                end=match.end(),
            )
        )

    return entities


def _extract_gazetteer(text: str) -> list[Entity]:
    """Gazetteer entities: landmarks, admin units, infrastructure assets."""
    lexicons = load_lexicons().get("entity_lexicon", {})
    tokens = tokenize(text)
    entities: list[Entity] = []

    for entity_type, key in (
        (EntityType.LANDMARK.value, "landmark"),
        (EntityType.ADMIN_UNIT.value, "admin_unit"),
        (EntityType.INFRA_ASSET.value, "infra_asset"),
    ):
        for term in lexicons.get(key, []):
            if contains_phrase(tokens, term):
                start = text.casefold().find(term.casefold())
                entities.append(
                    Entity(
                        type=entity_type,
                        text=term,
                        confidence=_GAZETTEER_CONFIDENCE,
                        start=max(0, start),
                        end=max(0, start) + len(term),
                    )
                )
    return entities


def capabilities() -> dict:
    """Describe the NER stage for ``GET /capabilities``."""
    lexicons = load_lexicons().get("entity_lexicon", {})
    return {
        "stage": "ner",
        "method": "rule_based_regex_and_gazetteer",
        "available": True,
        "configured_provider": settings.ner_provider,
        "model": None,
        "detail": {
            "gazetteer_terms": {key: len(terms) for key, terms in lexicons.items()},
            "entity_types": [entity.value for entity in EntityType],
        },
    }
