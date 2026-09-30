"""Small shared helpers: geometry maths, numeric guards, time, ids."""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.enums import Sector

# Coefficient weights used by the sector->asset-type mapping in
# `capacity_service`. One unit of each type serves a different number of
# people, which is what makes raw asset counts meaningless on their own.
ASSET_POPULATION_CAPACITY: dict[str, int] = {
    "WATER_SUPPLY": 1_500,
    "BOREWELL": 800,
    "ELECTRICITY_POLE": 250,
    "TRANSFORMER": 1_200,
    "STREET_LIGHT": 400,
    "ROAD": 0,
    "DRAIN": 900,
    "SEWAGE_LINE": 2_500,
    "WASTE_BIN": 300,
    "HEALTH_CLINIC": 5_000,
    "SCHOOL": 1_200,
    "BUS_STOP": 800,
    "PUBLIC_TOILET": 600,
    "WIFI_HOTSPOT": 700,
}

# Which asset types provide capacity for each sector.
SECTOR_ASSET_TYPES: dict[str, list[str]] = {
    Sector.WATER.value: ["WATER_SUPPLY", "BOREWELL"],
    Sector.ELECTRICITY.value: ["TRANSFORMER", "ELECTRICITY_POLE"],
    Sector.ROAD.value: ["ROAD"],
    Sector.SANITATION.value: ["SEWAGE_LINE", "DRAIN", "WASTE_BIN", "PUBLIC_TOILET"],
    Sector.HEALTHCARE.value: ["HEALTH_CLINIC"],
    Sector.EDUCATION.value: ["SCHOOL"],
    Sector.TRANSPORT.value: ["BUS_STOP"],
    Sector.DIGITAL_CONNECTIVITY.value: ["WIFI_HOTSPOT"],
}

# Condition grades ordered worst-first for quality aggregation.
CONDITION_SEVERITY: dict[str, int] = {
    "GOOD": 0,
    "FAIR": 1,
    "POOR": 2,
    "CRITICAL": 3,
}

_PINCODE_RE = re.compile(r"^\d{6}$")


def utcnow() -> datetime:
    """Timezone-aware UTC now, used for every timestamp column."""
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Short, prefixed, collision-resistant id for externally created rows."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Constrain ``value`` to ``[minimum, maximum]``."""
    return max(minimum, min(maximum, value))


def mean(values: list[float]) -> float:
    """Arithmetic mean that tolerates an empty sample."""
    return sum(values) / len(values) if values else 0.0


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that returns ``default`` instead of raising or dividing by zero."""
    return numerator / denominator if denominator else default


def normalise_0_100(value: float, maximum: float) -> float:
    """Scale ``value`` into 0-100 against ``maximum``."""
    return clamp(safe_div(value, maximum) * 100.0, 0.0, 100.0)


def haversine_metres(
    lat1: float, lon1: float, lat2: float, lon2: float, radius_m: float = 6_371_008.8
) -> float:
    """Great-circle distance in metres between two points.

    Haversine (not Vincenty/Puig) is deliberate: at ward-centroid resolution the
    error is centimetres, and it is dependency-free and easy to audit.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_m * math.asin(math.sqrt(a))


def bounding_box(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """Axis-aligned box around a point: ``(min_lat, max_lat, min_lon, max_lon)``.

    Used as a cheap pre-filter so only plausible candidate wards are distance
    tested rather than the whole country.
    """
    lat_delta = math.degrees(radius_m / 6_371_008.8)
    # Longitude degrees shrink with latitude; guard the poles so the box never
    # inverts and the pre-filter can never exclude a valid candidate.
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    lon_delta = math.degrees(radius_m / (6_371_008.8 * cos_lat))
    return (lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta)


def is_valid_pincode(value: Optional[str]) -> bool:
    """Indian pincode shape check: exactly six digits."""
    return bool(value) and bool(_PINCODE_RE.match(value or ""))


def is_valid_latitude(value: Optional[float]) -> bool:
    return value is not None and -90.0 <= value <= 90.0


def is_valid_longitude(value: Optional[float]) -> bool:
    return value is not None and -180.0 <= value <= 180.0


def round_or_none(value: Optional[float], digits: int = 2) -> Optional[float]:
    """Round for stable JSON output, preserving ``None``."""
    return None if value is None else round(value, digits)