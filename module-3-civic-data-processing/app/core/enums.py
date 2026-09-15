"""Canonical enumerations shared by models, services and API schemas."""

from __future__ import annotations

from enum import Enum
from typing import Final


class Category(str, Enum):
    ROAD = "ROAD"
    WATER = "WATER"
    ELECTRICITY = "ELECTRICITY"
    HEALTHCARE = "HEALTHCARE"
    EDUCATION = "EDUCATION"
    SANITATION = "SANITATION"
    TRANSPORT = "TRANSPORT"
    DIGITAL_CONNECTIVITY = "DIGITAL_CONNECTIVITY"
    HOUSING = "HOUSING"
    PUBLIC_SAFETY = "PUBLIC_SAFETY"
    # Internal sentinel: normalization could not classify the record.
    UNKNOWN = "UNKNOWN"


# Sentinel used when normalization cannot classify a record at all.
UNKNOWN_CATEGORY: Final[str] = Category.UNKNOWN.value

# Categories that a downstream (Module 4+) consumer considers actionable.
ALLOWED_CATEGORIES: Final[tuple[str, ...]] = (
    Category.ROAD.value,
    Category.WATER.value,
    Category.ELECTRICITY.value,
    Category.HEALTHCARE.value,
    Category.EDUCATION.value,
    Category.SANITATION.value,
    Category.TRANSPORT.value,
    Category.DIGITAL_CONNECTIVITY.value,
    Category.HOUSING.value,
    Category.PUBLIC_SAFETY.value,
)


class SubCategory(str, Enum):
    # ROAD
    DAMAGED_ROAD = "DAMAGED_ROAD"
    ROAD_SAFETY = "ROAD_SAFETY"
    TRAFFIC_MANAGEMENT = "TRAFFIC_MANAGEMENT"
    # WATER
    DRINKING_WATER = "DRINKING_WATER"
    WATER_SUPPLY_DISRUPTION = "WATER_SUPPLY_DISRUPTION"
    WATER_QUALITY = "WATER_QUALITY"
    IRRIGATION = "IRRIGATION"
    # ELECTRICITY
    POWER_OUTAGE = "POWER_OUTAGE"
    STREET_LIGHTING = "STREET_LIGHTING"
    TRANSFORMER_ISSUE = "TRANSFORMER_ISSUE"
    ELECTRICITY_BILLING = "ELECTRICITY_BILLING"
    # HEALTHCARE
    PRIMARY_HEALTHCARE = "PRIMARY_HEALTHCARE"
    HOSPITAL_ACCESS = "HOSPITAL_ACCESS"
    MEDICINE_SHORTAGE = "MEDICINE_SHORTAGE"
    AMBULANCE_SERVICE = "AMBULANCE_SERVICE"
    # EDUCATION
    SCHOOL_INFRASTRUCTURE = "SCHOOL_INFRASTRUCTURE"
    TEACHER_SHORTAGE = "TEACHER_SHORTAGE"
    SCHOOL_ACCESS = "SCHOOL_ACCESS"
    # SANITATION
    SEWAGE = "SEWAGE"
    DRAINAGE = "DRAINAGE"
    SOLID_WASTE = "SOLID_WASTE"
    PUBLIC_TOILETS = "PUBLIC_TOILETS"
    # TRANSPORT
    BRIDGE_DAMAGE = "BRIDGE_DAMAGE"
    PUBLIC_TRANSPORT = "PUBLIC_TRANSPORT"
    BUS_SHELTER = "BUS_SHELTER"
    ROAD_TRANSPORT = "ROAD_TRANSPORT"
    # DIGITAL_CONNECTIVITY
    INTERNET_CONNECTIVITY = "INTERNET_CONNECTIVITY"
    MOBILE_NETWORK = "MOBILE_NETWORK"
    PUBLIC_WIFI = "PUBLIC_WIFI"
    # HOUSING
    HOUSING_SCHEME = "HOUSING_SCHEME"
    PUBLIC_HOUSING = "PUBLIC_HOUSING"
    LAND_ENCROACHMENT = "LAND_ENCROACHMENT"
    # PUBLIC_SAFETY
    STREET_CRIME = "STREET_CRIME"
    WOMEN_SAFETY = "WOMEN_SAFETY"
    FIRE_SAFETY = "FIRE_SAFETY"
    DISASTER_RESPONSE = "DISASTER_RESPONSE"
    # Internal sentinel
    UNCLASSIFIED = "UNCLASSIFIED"


class DataQualityStatus(str, Enum):
    VALID = "VALID"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INVALID = "INVALID"


class ProcessingStatus(str, Enum):
    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class LocationStatus(str, Enum):
    RESOLVED = "RESOLVED"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"


class IssueGroupStatus(str, Enum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class EventStatus(str, Enum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class SeverityFinding(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# Maps raw/aliased language values (including endonyms) to ISO 639-1 codes.
LANGUAGE_ALIASES: Final[dict[str, str]] = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "ta": "ta",
    "tam": "ta",
    "tamil": "ta",
    "தமிழ்": "ta",
    "hi": "hi",
    "hin": "hi",
    "hindi": "hi",
    "हिन्दी": "hi",
    "हिंदी": "hi",
    "te": "te",
    "tel": "te",
    "telugu": "te",
    "తెలుగు": "te",
    "bn": "bn",
    "ben": "bn",
    "bengali": "bn",
    "bangla": "bn",
    "বাংলা": "bn",
    "kn": "kn",
    "kan": "kn",
    "kannada": "kn",
    "ಕನ್ನಡ": "kn",
    "ml": "ml",
    "mal": "ml",
    "malayalam": "ml",
    "മലയാളം": "ml",
    "mr": "mr",
    "marathi": "mr",
    "मराठी": "mr",
    "pt": "pt",
    "por": "pt",
    "portuguese": "pt",
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
    "zh": "zh",
    "zho": "zh",
    "chinese": "zh",
    "中文": "zh",
}

SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("en", "ta", "hi", "te", "bn", "kn", "ml")
