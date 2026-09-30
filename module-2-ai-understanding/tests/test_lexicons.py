"""The lexicon files are data; these tests keep that data honest.

A keyword file drifting away from the enum vocabulary is the easiest way to
silently break classification (an unknown sub-category would be persisted and
downstream routers would never match it), so it is validated here rather than
discovered in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.enums import Category, SeverityBand, SubCategory

LEXICON_PATH = Path(__file__).resolve().parent.parent / "app" / "resources" / "lexicons.json"

# Non-band keys under severity_lexicon: signal lists consumed by name rather
# than being mapped to a SeverityBand.
_NON_BAND_KEYS = {"mitigator", "personal_health"}


@pytest.fixture(scope="module")
def lexicons() -> dict:
    return json.loads(LEXICON_PATH.read_text(encoding="utf-8"))


def _assert_valid_section(section: dict, lexicons: dict) -> None:
    valid_categories = {e.value for e in Category}
    valid_subcategories = {e.value for e in SubCategory}

    for category, mapping in section.items():
        if category.startswith("_"):  # documentation key
            continue
        assert category in valid_categories, f"{category!r} is not a valid Category"
        assert mapping, f"{category!r} has no sub-categories"
        for sub_category, terms in mapping.items():
            assert sub_category in valid_subcategories, (
                f"{category}/{sub_category!r} is not a valid SubCategory"
            )
            assert isinstance(terms, list) and terms, (
                f"{category}/{sub_category} must be a non-empty list of terms"
            )
            for term in terms:
                assert isinstance(term, str) and term.strip(), (
                    f"{category}/{sub_category} has an empty term"
                )
                assert term == term.strip(), (
                    f"{category}/{sub_category} term {term!r} has stray whitespace - "
                    "stray spaces silently stop phrases from matching"
                )


def test_category_keywords_are_valid(lexicons: dict) -> None:
    _assert_valid_section(lexicons["category_keywords"], lexicons)


def test_category_lemmas_are_valid(lexicons: dict) -> None:
    _assert_valid_section(lexicons["category_lemmas"], lexicons)


def test_severity_bands_are_valid(lexicons: dict) -> None:
    valid_bands = {e.value.lower() for e in SeverityBand}
    for band, terms in lexicons["severity_lexicon"].items():
        if band.startswith("_") or band in _NON_BAND_KEYS:
            continue
        assert band.lower() in valid_bands, f"{band!r} is not a valid SeverityBand"
        assert terms, f"severity band {band!r} is empty"


def test_no_phrase_is_shared_by_two_subcategories(lexicons: dict) -> None:
    """A phrase listed under two sub-categories ties them and picks arbitrarily.

    "no water supply" in both WATER_SUPPLY_DISRUPTION and DRINKING_WATER would
    make the winner depend on dict ordering, so drift is caught here instead of
    showing up as an unpredictable label in production.
    """
    offenders: dict[str, list[str]] = {}
    for section in ("category_keywords", "category_lemmas"):
        owners: dict[str, list[str]] = {}
        for category, mapping in lexicons[section].items():
            if category.startswith("_"):
                continue
            for sub_category, terms in mapping.items():
                for term in terms:
                    owners.setdefault(term, []).append(f"{section}:{category}/{sub_category}")
        for term, places in owners.items():
            if len(places) > 1:
                offenders[term] = places
    assert not offenders, f"phrases shared by multiple sub-categories: {offenders}"


def test_every_category_has_keywords(lexicons: dict) -> None:
    """No category should be unreachable - an empty category is a silent gap."""
    keyword_categories = {
        c for c in lexicons["category_keywords"] if not c.startswith("_")
    }
    assert keyword_categories == {e.value for e in Category if e.value != Category.UNKNOWN.value}


def test_terms_are_lowercase(lexicons: dict) -> None:
    """Matching normalises input to lowercase, so mixed-case terms never fire."""
    offenders = []
    for section in ("category_keywords", "category_lemmas"):
        for category, mapping in lexicons[section].items():
            if category.startswith("_"):
                continue
            for sub_category, terms in mapping.items():
                offenders.extend(
                    f"{section}/{category}/{sub_category}: {term!r}"
                    for term in terms
                    if term != term.lower()
                )
    assert not offenders, f"mixed-case lexicon terms will never match: {offenders}"