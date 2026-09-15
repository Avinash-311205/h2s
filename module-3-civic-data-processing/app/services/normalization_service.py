"""Normalization service.

Maps the free-text, multilingual output of Module 2 onto the platform's
canonical ``(category, sub_category)`` vocabulary.

This is *standardization*, not translation: Module 2 owns language
understanding. Here we only reconcile already-understood values against the
controlled vocabulary shipped in
``app/resources/normalization_dictionary.json``.

The dictionary is data-driven, so adding a new language (or another BRICS
language) is a matter of extending the JSON file - no code changes required.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.core.enums import ALLOWED_CATEGORIES, Category, SubCategory, UNKNOWN_CATEGORY
from app.core.logging import get_logger
from app.core.utils import normalize_description, normalize_key, normalize_text

logger = get_logger(__name__)

DICTIONARY_PATH = Path(__file__).resolve().parents[1] / "resources" / "normalization_dictionary.json"

UNCLASSIFIED = SubCategory.UNCLASSIFIED.value

# Applied when a keyword is found in the language the record was written in.
LANGUAGE_BOOST = 3.0


@dataclass
class NormalizationResult:
    category: str
    sub_category: str
    confidence: float = 0.0
    match_source: str = "fallback"
    matched_keywords: list[str] = field(default_factory=list)
    matched_alias: Optional[str] = None
    language: Optional[str] = None
    category_mismatch: bool = False
    conflicting_sub_category: Optional[str] = None

    @property
    def category_known(self) -> bool:
        return self.category in ALLOWED_CATEGORIES

    @property
    def sub_category_known(self) -> bool:
        return self.sub_category != UNCLASSIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "sub_category": self.sub_category,
            "confidence": round(self.confidence, 3),
            "match_source": self.match_source,
            "matched_keywords": self.matched_keywords,
            "matched_alias": self.matched_alias,
            "language": self.language,
        }


class NormalizationDictionary:
    """In-memory index built from the on-disk vocabulary file."""

    def __init__(self, data: dict[str, Any]):
        self.version: str = str(data.get("version", "0"))
        self.languages: tuple[str, ...] = tuple(data.get("languages") or ())

        # normalized alias -> {category}
        self.category_aliases: dict[str, set[str]] = {}
        # normalized alias -> {(category, sub_category)}
        self.sub_category_aliases: dict[str, set[tuple[str, str]]] = {}
        # normalized keyword -> [(category, sub_category, raw_keyword, language)]
        self._keyword_index: dict[str, list[tuple[str, str, str, str]]] = {}
        # normalized category-only keyword -> [(category, raw_keyword, language)]
        self._category_keyword_index: dict[str, list[tuple[str, str, str]]] = {}
        self.sub_categories_by_category: dict[str, set[str]] = {}

        categories = data.get("categories") or {}

        for category, payload in categories.items():
            self.category_aliases.setdefault(normalize_key(category), set()).add(category)
            self.sub_categories_by_category.setdefault(category, set())

            for aliases in (payload.get("aliases") or {}).values():
                for alias in aliases:
                    key = normalize_key(alias)
                    if key:
                        self.category_aliases.setdefault(key, set()).add(category)

            for sub_category, sub_payload in (payload.get("sub_categories") or {}).items():
                self.sub_categories_by_category[category].add(sub_category)
                self.sub_category_aliases.setdefault(normalize_key(sub_category), set()).add(
                    (category, sub_category)
                )
                for alias in sub_payload.get("aliases") or []:
                    key = normalize_key(alias)
                    if key:
                        self.sub_category_aliases.setdefault(key, set()).add((category, sub_category))

                for language, keywords in (sub_payload.get("keywords") or {}).items():
                    for keyword in keywords:
                        key = normalize_text(keyword).casefold()
                        if key:
                            self._keyword_index.setdefault(key, []).append(
                                (category, sub_category, keyword, language)
                            )

        # Category-only keywords classify records that do not name a specific
        # sub-category ("road problem", "water issue").
        for category, payload in categories.items():
            for language, aliases in (payload.get("aliases") or {}).items():
                for alias in aliases:
                    key = normalize_text(alias).casefold()
                    if key:
                        self._category_keyword_index.setdefault(key, []).append((category, alias, language))

        # Longest keywords first: specific phrases beat generic tokens.
        self._keyword_order = sorted(self._keyword_index, key=len, reverse=True)
        self._category_keyword_order = sorted(self._category_keyword_index, key=len, reverse=True)

    # ------------------------------------------------------------------
    @property
    def keyword_count(self) -> int:
        return len(self._keyword_index)

    @property
    def category_count(self) -> int:
        return len(self.sub_categories_by_category)

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "languages": list(self.languages),
            "categories": self.category_count,
            "sub_categories": sum(len(v) for v in self.sub_categories_by_category.values()),
            "keywords": self.keyword_count,
        }

    # ------------------------------------------------------------------
    def resolve_category(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        candidates = self.category_aliases.get(normalize_key(value))
        if not candidates:
            return None
        if len(candidates) == 1:
            return next(iter(candidates))
        # Ambiguous alias: prefer a canonical, actionable category.
        for candidate in sorted(candidates):
            if candidate in ALLOWED_CATEGORIES:
                return candidate
        return sorted(candidates)[0]

    def resolve_sub_category(
        self, value: Optional[str], category_hint: Optional[str] = None
    ) -> tuple[Optional[str], Optional[str], bool]:
        """Return ``(category, sub_category, mismatch)``.

        ``mismatch`` is ``True`` when the alias is known but belongs to a
        different category than the one supplied by Module 2.
        """
        if not value:
            return None, None, False

        candidates = self.sub_category_aliases.get(normalize_key(value))
        if not candidates:
            return None, None, False

        if category_hint:
            for candidate_category, sub_category in candidates:
                if candidate_category == category_hint:
                    return category_hint, sub_category, False
            return None, None, True

        ordered = sorted(candidates, key=lambda item: (item[0] not in ALLOWED_CATEGORIES, item[0]))
        return ordered[0][0], ordered[0][1], False

    def find_sub_categories(
        self,
        text: str,
        language: Optional[str] = None,
        category_hint: Optional[str] = None,
    ) -> list[tuple[str, str, str, float]]:
        """Best keyword matches as ``(category, sub_category, keyword, score)``."""
        if not text:
            return []

        matches: list[tuple[str, str, str, float]] = []
        seen: set[tuple[str, str]] = set()

        for keyword in self._keyword_order:
            if keyword not in text:
                continue
            for category, sub_category, raw_keyword, keyword_language in self._keyword_index[keyword]:
                if category_hint and category != category_hint:
                    continue
                key = (category, sub_category)
                if key in seen:
                    continue
                score = float(len(keyword)) + (LANGUAGE_BOOST if keyword_language == language else 0.0)
                matches.append((category, sub_category, raw_keyword, score))
                seen.add(key)

        matches.sort(key=lambda item: item[3], reverse=True)
        return matches

    def find_categories(self, text: str, language: Optional[str] = None) -> list[tuple[str, str, float]]:
        """Best category-only matches as ``(category, keyword, score)``."""
        if not text:
            return []

        matches: list[tuple[str, str, float]] = []
        seen: set[str] = set()

        for keyword in self._category_keyword_order:
            if keyword not in text:
                continue
            for category, raw_keyword, keyword_language in self._category_keyword_index[keyword]:
                if category in seen:
                    continue
                score = float(len(keyword)) + (LANGUAGE_BOOST if keyword_language == language else 0.0)
                matches.append((category, raw_keyword, score))
                seen.add(category)

        matches.sort(key=lambda item: item[2], reverse=True)
        return matches


@lru_cache
def get_dictionary() -> NormalizationDictionary:
    with DICTIONARY_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    dictionary = NormalizationDictionary(data)
    logger.info("normalization_dictionary_loaded", extra=dictionary.summary())
    return dictionary


def reload_dictionary() -> NormalizationDictionary:
    """Drop the cache - handy in tests and after editing the vocabulary file."""
    get_dictionary.cache_clear()
    return get_dictionary()


class NormalizationService:
    """Standardizes a cleaned record onto the canonical civic vocabulary.

    Resolution order (deterministic):

    1. Explicit ``category`` / ``sub_category`` values from Module 2 are mapped
       through the alias tables.
    2. Missing values are inferred from keyword evidence in the description,
       restricted to the explicit category when one was provided.
    3. Anything still unknown falls back to ``UNKNOWN`` / ``UNCLASSIFIED``,
       which the validation service surfaces as ``INVALID`` / ``NEEDS_REVIEW``.
    """

    def __init__(self, dictionary: Optional[NormalizationDictionary] = None):
        self.dictionary = dictionary or get_dictionary()

    def normalize(
        self,
        description: Optional[str] = None,
        category: Optional[str] = None,
        sub_category: Optional[str] = None,
        language: Optional[str] = None,
    ) -> NormalizationResult:
        text = normalize_description(description or "")
        matched_keywords: list[str] = []
        matched_alias: Optional[str] = None

        # --- step 1: explicit values --------------------------------------
        resolved_category = self.dictionary.resolve_category(category)
        resolved_sub_category: Optional[str] = None
        mismatch = False

        if sub_category:
            alias_category, alias_sub_category, mismatch = self.dictionary.resolve_sub_category(
                sub_category, resolved_category
            )
            if alias_sub_category and not mismatch:
                resolved_sub_category = alias_sub_category
                matched_alias = sub_category
                if resolved_category is None:
                    resolved_category = alias_category

        explicit_category = resolved_category
        inferred_sub_category: Optional[str] = None

        # --- step 2: infer from description ------------------------------
        if text and resolved_sub_category is None:
            candidates = self.dictionary.find_sub_categories(
                text, language=language, category_hint=resolved_category
            )
            if candidates:
                inferred_category, inferred_sub_category, keyword, _score = candidates[0]
                matched_keywords.append(keyword)
                if resolved_category is None:
                    resolved_category = inferred_category

        if text and resolved_category is None:
            category_candidates = self.dictionary.find_categories(text, language=language)
            if category_candidates:
                resolved_category, keyword, _score = category_candidates[0]
                matched_keywords.append(keyword)

        # --- step 3: fallbacks -------------------------------------------
        category_final = resolved_category or UNKNOWN_CATEGORY
        sub_category_final = resolved_sub_category or inferred_sub_category or UNCLASSIFIED

        # --- confidence ---------------------------------------------------
        if resolved_sub_category and explicit_category:
            confidence, source = 0.95, "explicit"
        elif resolved_sub_category:
            confidence, source = 0.90, "explicit"
        elif inferred_sub_category and explicit_category:
            confidence, source = 0.85, "explicit_category+inferred_sub_category"
        elif inferred_sub_category:
            confidence, source = 0.70, "inferred"
        elif category_final != UNKNOWN_CATEGORY:
            confidence, source = 0.55, "category_only"
        else:
            confidence, source = 0.0, "fallback"

        return NormalizationResult(
            category=category_final,
            sub_category=sub_category_final,
            confidence=confidence,
            match_source=source,
            matched_keywords=matched_keywords,
            matched_alias=matched_alias,
            language=language,
            category_mismatch=mismatch,
            conflicting_sub_category=sub_category if mismatch else None,
        )


# Convenience for callers that only need the canonical category constants.
CATEGORY_ROAD = Category.ROAD.value
