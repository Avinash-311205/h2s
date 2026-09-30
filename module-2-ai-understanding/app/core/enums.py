"""Canonical enumerations for Module 2 - AI Understanding.

The category / sub-category taxonomy is deliberately identical to the one used
by Module 3 (Civic Data Processing). That is the integration contract between
the two modules: whatever Module 2 infers is directly acceptable to Module 3's
normalization step, so a record never has to be re-labelled mid-pipeline.

Every value is uppercase and stable -- treat them as a versioned API surface.
"""

from __future__ import annotations

from enum import Enum
from typing import Final


class Category(str, Enum):
    """Top-level infrastructure taxonomy (mirrors Module 3)."""

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
    UNKNOWN = "UNKNOWN"


# Sentinel used when no category keyword matched with enough confidence.
UNKNOWN_CATEGORY: Final[str] = Category.UNKNOWN.value


class SubCategory(str, Enum):
    """Second-level taxonomy (mirrors Module 3)."""

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
    # EDUCATION
    SCHOOL_INFRASTRUCTURE = "SCHOOL_INFRASTRUCTURE"
    TEACHER_SHORTAGE = "TEACHER_SHORTAGE"
    SCHOOL_ACCESS = "SCHOOL_ACCESS"
    # HOUSING
    HOUSING_SCHEME = "HOUSING_SCHEME"
    PUBLIC_HOUSING = "PUBLIC_HOUSING"
    LAND_ENCROACHMENT = "LAND_ENCROACHMENT"
    # PUBLIC_SAFETY
    STREET_CRIME = "STREET_CRIME"
    WOMEN_SAFETY = "WOMEN_SAFETY"
    FIRE_SAFETY = "FIRE_SAFETY"
    DISASTER_RESPONSE = "DISASTER_RESPONSE"
    UNCLASSIFIED = "UNCLASSIFIED"


class MediaType(str, Enum):
    """Input modality handled by a Module 2 understanding request."""

    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    MULTIMODAL = "multimodal"


class UnderstandingStage(str, Enum):
    """Auditable stages of the understanding pipeline.

    A record stores one ``stage_status`` per stage so a reviewer can see
    exactly which capability produced (or failed to produce) each field.
    """

    LANGUAGE_DETECTION = "language_detection"
    ASR = "asr"
    TRANSLATION = "translation"
    NER = "ner"
    CLASSIFICATION = "classification"
    SEVERITY = "severity"
    IMAGE_ANALYSIS = "image_analysis"


class StageStatus(str, Enum):
    OK = "OK"
    SKIPPED = "SKIPPED"
    FALLBACK = "FALLBACK"
    FAILED = "FAILED"


class UnderstandingStatus(str, Enum):
    """Terminal state of a whole understanding run."""

    UNDERSTOOD = "UNDERSTOOD"  # all requested stages produced output
    PARTIAL = "PARTIAL"  # some stage degraded (e.g. translation unavailable)
    FAILED = "FAILED"  # nothing usable was produced


class EntityType(str, Enum):
    """Entity types extracted by the rule-based NER stage."""

    PINCODE = "PINCODE"
    PHONE = "PHONE"
    LANDMARK = "LANDMARK"
    ADMIN_UNIT = "ADMIN_UNIT"  # ward / village / taluk / district mentions
    INFRA_ASSET = "INFRA_ASSET"  # "the school", "the hospital"
    PERSON = "PERSON"
    DATE = "DATE"
    DURATION = "DURATION"  # "for 3 days", "since morning"
    COORDINATE = "COORDINATE"


class SeverityBand(str, Enum):
    """Human-facing severity bands for policymakers (1-5 scale)."""

    LOW = "LOW"  # 1-2: cosmetic / minor inconvenience
    MEDIUM = "MEDIUM"  # 3: notable disruption
    HIGH = "HIGH"  # 4: serious loss of service
    CRITICAL = "CRITICAL"  # 5: immediate danger to life / public health


# Numeric -> band lookup shared by the API and Module 6/7.
SEVERITY_BANDS: Final[dict[int, str]] = {
    1: SeverityBand.LOW.value,
    2: SeverityBand.LOW.value,
    3: SeverityBand.MEDIUM.value,
    4: SeverityBand.HIGH.value,
    5: SeverityBand.CRITICAL.value,
}


def band_for_severity(severity: int) -> str:
    """Map a 1-5 severity to its human band, clamped to the valid range."""
    clamped = max(1, min(5, int(severity)))
    return SEVERITY_BANDS[clamped]


class SourceChannel(str, Enum):
    """How the citizen reached Module 1 (carried through for analytics)."""

    WHATSAPP = "WHATSAPP"
    TELEGRAM = "TELEGRAM"
    IVR = "IVR"
    WEB = "WEB"
    MOBILE_APP = "MOBILE_APP"
    UNKNOWN = "UNKNOWN"
