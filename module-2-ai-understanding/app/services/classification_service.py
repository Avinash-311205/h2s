"""Category / sub-category classification and 1-5 severity scoring.

Both are evidence-based, not model-based, and both are explainable: the
services return *which keywords fired* alongside the label, so a policymaker
or judge can always answer "why is this ROAD and not WATER?".

Category confidence is the share of the winning category's keyword hits
relative to all category hits -- a transparent, reproducible score rather than
an opaque softmax over a fine-tuned network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings
from app.core.enums import Category, StageStatus, SubCategory, band_for_severity
from app.core.logging import get_logger
from app.core.utils import clamp, contains_phrase, tokenize
from app.services.language_service import load_lexicons

logger = get_logger(__name__)


@dataclass
class CategoryPrediction:
    """The chosen category plus the evidence behind it."""

    category: str
    sub_category: str
    confidence: float
    matched_keywords: list[str] = field(default_factory=list)
    runner_up: Optional[str] = None
    runner_up_confidence: float = 0.0

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "sub_category": self.sub_category,
            "confidence": round(self.confidence, 4),
            "matched_keywords": self.matched_keywords,
            "runner_up": self.runner_up,
            "runner_up_confidence": round(self.runner_up_confidence, 4),
        }


@dataclass
class SeverityPrediction:
    """The 1-5 severity score plus the modifiers that produced it."""

    severity: int
    band: str
    confidence: float
    base_severity: int
    matched_keywords: list[str] = field(default_factory=list)
    longest_duration_days: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "band": self.band,
            "confidence": round(self.confidence, 4),
            "base_severity": self.base_severity,
            "matched_keywords": self.matched_keywords,
            "longest_duration_days": self.longest_duration_days,
        }


# Severity band -> base value. Order matters: the strongest evidence wins.
_BAND_BASE = {"critical": 5, "high": 4, "medium": 3, "mitigator": 1}
# Duration escalation: a problem persisting this long is worth one extra point.
_DURATION_ESCALATION_DAYS = 7
# Personal-health routing: a citizen reporting illness is a high-severity
# need regardless of infrastructure keyword density.
_PERSONAL_HEALTH_SEVERITY = 4
# "already fixed", "slight", "resolved" cap severity instead of being ignored:
# a resolved complaint should never page an officer as if it were open.
_MITIGATOR_CAP = 2
# A single strong lemma ("pothole", "sewage") is weaker evidence than a full
# phrase ("no water supply for days"), so it scores this fraction of a hit.
# This is what lets "road is slightly damaged" classify without letting one
# generic word outvote multi-word evidence.
LEMMA_WEIGHT = 0.5


def classify_category(text: str) -> CategoryPrediction:
    """Assign a category + sub-category from keyword evidence.

    Scoring is evidence-weighted:

    * a matched multi-word phrase = 1.0 point
    * a matched single-word lemma = ``LEMMA_WEIGHT`` points

    Confidence is the winning category's share of all points, so a single
    decisive phrase still yields high confidence, while a weak generic word
    cannot outvote richer evidence from another category.

    Returns ``Category.UNKNOWN`` with zero confidence when nothing matched;
    downstream stages (and Module 3) can then route the record for review
    instead of confidently mislabelling it.
    """
    canonical_tokens = tokenize(text)
    if not canonical_tokens:
        return CategoryPrediction(
            category=Category.UNKNOWN.value,
            sub_category=SubCategory.UNCLASSIFIED.value,
            confidence=0.0,
        )

    lexicons = load_lexicons()
    keywords_by_category = lexicons.get("category_keywords", {})
    lemmas_by_category = lexicons.get("category_lemmas", {})
    # Lexicon files carry "_comment" / "_comment"-style documentation keys.
    keywords_by_category = {k: v for k, v in keywords_by_category.items() if not k.startswith("_")}
    lemmas_by_category = {k: v for k, v in lemmas_by_category.items() if not k.startswith("_")}

    # Phrase evidence, attributed to the winning sub-category.
    scores: dict[str, list[tuple[str, list[str], float]]] = {}
    for category, subcategories in keywords_by_category.items():
        for sub_category, terms in subcategories.items():
            hits = [term for term in terms if contains_phrase(canonical_tokens, term)]
            if hits:
                scores.setdefault(category, []).append((sub_category, hits, float(len(hits))))

    # Lemma evidence: weighted, and carries its own sub-category so a
    # lemma-only hit still produces a meaningful label.
    lemma_scores: dict[str, list[tuple[str, list[str], float]]] = {}
    for category, subcategories in lemmas_by_category.items():
        for sub_category, lemmas in subcategories.items():
            hits = [lemma for lemma in lemmas if contains_phrase(canonical_tokens, lemma)]
            if hits:
                lemma_scores.setdefault(category, []).append(
                    (sub_category, hits, len(hits) * LEMMA_WEIGHT)
                )

    if not scores and not lemma_scores:
        return CategoryPrediction(
            category=Category.UNKNOWN.value,
            sub_category=SubCategory.UNCLASSIFIED.value,
            confidence=0.0,
        )

    def _category_points(entries_map: dict) -> dict[str, float]:
        return {
            category: sum(points for _, _, points in entries)
            for category, entries in entries_map.items()
        }

    phrase_totals = _category_points(scores)
    lemma_totals = _category_points(lemma_scores)

    category_totals: dict[str, float] = dict(phrase_totals)
    for category, points in lemma_totals.items():
        category_totals[category] = category_totals.get(category, 0.0) + points

    grand_total = sum(category_totals.values())

    ranked = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
    winning_category, winning_points = ranked[0]
    confidence = clamp(winning_points / grand_total if grand_total else 0.0, 0.0, 1.0)

    # Prefer the winning sub-category from phrase evidence (multi-word beats a
    # single lemma); fall back to lemma evidence when no phrase fired.
    phrase_entries = scores.get(winning_category)
    lemma_entries = lemma_scores.get(winning_category)

    if phrase_entries:
        best_sub, best_hits, _ = max(phrase_entries, key=lambda entry: entry[2])
        matched = list(best_hits[:10])
        if lemma_entries:
            matched.extend(lemma for _, hits, _ in lemma_entries for lemma in hits)
    elif lemma_entries:
        best_sub, best_hits, _ = max(lemma_entries, key=lambda entry: entry[2])
        matched = list(best_hits[:10])
    else:
        best_sub = SubCategory.UNCLASSIFIED.value
        matched = []

    matched = matched[:10]
    runner_up, runner_up_points = (ranked[1] if len(ranked) > 1 else (None, 0.0))

    # Below the configured floor we refuse to commit to a label.
    if confidence < settings.classification_min_confidence:
        return CategoryPrediction(
            category=Category.UNKNOWN.value,
            sub_category=SubCategory.UNCLASSIFIED.value,
            confidence=round(confidence, 4),
            matched_keywords=matched,
            runner_up=winning_category,
            runner_up_confidence=round(confidence, 4),
        )

    return CategoryPrediction(
        category=winning_category,
        sub_category=best_sub,
        confidence=round(confidence, 4),
        matched_keywords=matched,
        runner_up=runner_up,
        runner_up_confidence=round(runner_up_points / grand_total, 4) if grand_total else 0.0,
    )


def score_severity(
    text: str,
    prediction: Optional[CategoryPrediction] = None,
    max_duration_days: Optional[int] = None,
) -> SeverityPrediction:
    """Score severity on the shared 1-5 civic scale.

    The rule is intentionally simple and inspectable:

    1. Take the base severity from the strongest keyword band present.
    2. Add one point when the complaint has persisted a week or longer.
    3. Raise to at least high when personal-health language is present.
    4. Cap at low when mitigator language is present ("already fixed").
    5. Clamp to ``[min_severity, max_severity]`` -- never 0.
    """
    canonical_tokens = tokenize(text)
    lexicon = load_lexicons().get("severity_lexicon", {})

    base_severity = 2  # a complaint with no explicit signal is a real complaint
    matched: list[str] = []
    confidence = 0.35

    # Strongest harm evidence first: critical > high > medium.
    for band in ("critical", "high", "medium"):
        terms = lexicon.get(band, [])
        hits = [term for term in terms if contains_phrase(canonical_tokens, term)]
        if hits:
            base_severity = _BAND_BASE[band]
            matched.extend(hits[:5])
            confidence = max(confidence, {"critical": 0.9, "high": 0.8, "medium": 0.6}[band])
            break  # the strongest band wins outright

    severity = base_severity

    # Mitigators are evaluated independently of the harm bands: "there was a
    # pothole but it is already fixed" matches a harm phrase *and* a mitigator,
    # and the citizen is telling us the problem is closed.
    mitigator_hits = [
        term
        for term in lexicon.get("mitigator", [])
        if contains_phrase(canonical_tokens, term)
    ]

    # Persistence escalation.
    if max_duration_days is not None and max_duration_days >= _DURATION_ESCALATION_DAYS:
        severity += 1
        matched.append(f"persisting {max_duration_days}+ days")
        confidence = max(confidence, 0.7)

    # Personal-health routing.
    if settings.personal_health_routing:
        personal_hits = [
            term
            for term in lexicon.get("personal_health", [])
            if contains_phrase(canonical_tokens, term)
        ]
        if personal_hits:
            severity = max(severity, _PERSONAL_HEALTH_SEVERITY)
            matched.extend(personal_hits[:3])
            confidence = max(confidence, 0.75)

    if mitigator_hits:
        severity = min(severity, _MITIGATOR_CAP)
        matched.extend(mitigator_hits[:3])
        confidence = max(confidence, 0.5)

    severity = int(clamp(severity, settings.min_severity, settings.max_severity))

    return SeverityPrediction(
        severity=severity,
        band=band_for_severity(severity),
        confidence=round(clamp(confidence, 0.0, 1.0), 4),
        base_severity=base_severity,
        matched_keywords=matched[:10],
        longest_duration_days=max_duration_days,
    )


def capabilities() -> dict:
    """Describe the classification stage for ``GET /capabilities``."""
    keywords = load_lexicons().get("category_keywords", {})
    total_terms = sum(
        len(terms)
        for subcategories in keywords.values()
        for terms in subcategories.values()
    )
    return {
        "stage": "classification",
        "method": "multilingual_keyword_evidence",
        "available": True,
        "configured_provider": "multilingual_lexicon",
        "model": None,
        "detail": {
            "categories": len([k for k in keywords if not k.startswith("_")]),
            "keyword_terms": total_terms,
            "min_confidence": settings.classification_min_confidence,
            "severity_scale": [settings.min_severity, settings.max_severity],
        },
    }
