"""Geometry: point-to-ward assignment.

Wards are stored as a bounding box plus a centroid rather than a full polygon.
That is a deliberate trade-off: a rectangle is enough to answer "which ward does
this complaint belong to" at the resolution Module 3 already produces, it keeps
the module dependency-free (no Shapely/GeoPandas), and a complaint outside every
bounding box is reported as unassigned instead of being snapped to a distant
ward and silently corrupting that ward's statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from app.core.config import settings
from app.core.utils import (
    bounding_box,
    haversine_metres,
    is_valid_latitude,
    is_valid_longitude,
    round_or_none,
)


@dataclass(frozen=True)
class WardAssignment:
    """Result of locating a point against the ward layer."""

    ward_code: Optional[str]
    ward_name: Optional[str]
    district: Optional[str]
    latitude: float
    longitude: float
    distance_m: Optional[float]
    method: str
    confident: bool

    def as_dict(self) -> dict:
        return {
            "ward_code": self.ward_code,
            "ward_name": self.ward_name,
            "district": self.district,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "distance_m": round_or_none(self.distance_m),
            "method": self.method,
            "confident": self.confident,
        }


@dataclass(frozen=True)
class _Candidate:
    ward_code: str
    name: str
    district: str
    centroid_latitude: float
    centroid_longitude: float
    min_latitude: Optional[float]
    max_latitude: Optional[float]
    min_longitude: Optional[float]
    max_longitude: Optional[float]


def _contains(candidate: _Candidate, latitude: float, longitude: float) -> bool:
    """Bounding-box containment test; open bounds are treated as no constraint."""
    if candidate.min_latitude is not None and latitude < candidate.min_latitude:
        return False
    if candidate.max_latitude is not None and latitude > candidate.max_latitude:
        return False
    if candidate.min_longitude is not None and longitude < candidate.min_longitude:
        return False
    if candidate.max_longitude is not None and longitude > candidate.max_longitude:
        return False
    return True


def assign_to_ward(
    latitude: Optional[float],
    longitude: Optional[float],
    wards: Sequence,
    radius_m: Optional[float] = None,
) -> WardAssignment:
    """Locate a point in the ward layer.

    Resolution order:

    1. Reject invalid or missing coordinates outright.
    2. Prefer a ward whose bounding box contains the point (``bbox``).
    3. Otherwise fall back to the nearest centroid, but only within
       ``max_assignment_distance_m`` -- beyond that the point is unassigned.

    Args:
        latitude / longitude: the point to locate.
        wards: iterable of :class:`app.models.mesh_tables.Ward` rows.
        radius_m: earth radius override (defaults to the configured value).

    Returns:
        A :class:`WardAssignment`. ``confident`` is ``False`` when the match came
        from the nearest-centroid fallback rather than a bounding box, so
        downstream consumers can weight it differently.
    """
    radius = radius_m or settings.earth_radius_m
    max_distance = settings.max_assignment_distance_m

    if not is_valid_latitude(latitude) or not is_valid_longitude(longitude):
        return WardAssignment(
            ward_code=None,
            ward_name=None,
            district=None,
            latitude=latitude if latitude is not None else 0.0,
            longitude=longitude if longitude is not None else 0.0,
            distance_m=None,
            method="invalid_coordinates",
            confident=False,
        )

    assert latitude is not None and longitude is not None  # for type checkers
    candidates = [_Candidate(**_ward_fields(ward)) for ward in wards]
    if not candidates:
        return WardAssignment(
            ward_code=None, ward_name=None, district=None,
            latitude=latitude, longitude=longitude,
            distance_m=None, method="empty_ward_layer", confident=False,
        )

    # --- 1. bounding-box hit -------------------------------------------------
    for candidate in candidates:
        if _contains(candidate, latitude, longitude):
            distance = haversine_metres(
                latitude, longitude,
                candidate.centroid_latitude, candidate.centroid_longitude, radius,
            )
            return WardAssignment(
                ward_code=candidate.ward_code,
                ward_name=candidate.name,
                district=candidate.district,
                latitude=latitude,
                longitude=longitude,
                distance_m=distance,
                method="bounding_box",
                confident=True,
            )

    # --- 2. nearest centroid within tolerance --------------------------------
    # Cheap bbox pre-filter, then exact haversine on the survivors only.
    near_lat_min, near_lat_max, near_lon_min, near_lon_max = bounding_box(
        latitude, longitude, max_distance
    )
    nearest: Optional[tuple[float, _Candidate]] = None
    for candidate in candidates:
        # Skip candidates whose own box is far away before doing haversine.
        if candidate.max_latitude is not None and candidate.max_latitude < near_lat_min:
            continue
        if candidate.min_latitude is not None and candidate.min_latitude > near_lat_max:
            continue
        if candidate.max_longitude is not None and candidate.max_longitude < near_lon_min:
            continue
        if candidate.min_longitude is not None and candidate.min_longitude > near_lon_max:
            continue

        distance = haversine_metres(
            latitude, longitude,
            candidate.centroid_latitude, candidate.centroid_longitude, radius,
        )
        if nearest is None or distance < nearest[0]:
            nearest = (distance, candidate)

    if nearest is None or nearest[0] > max_distance:
        return WardAssignment(
            ward_code=None, ward_name=None, district=None,
            latitude=latitude, longitude=longitude,
            distance_m=round_or_none(nearest[0]) if nearest else None,
            method="out_of_range",
            confident=False,
        )

    distance, candidate = nearest
    return WardAssignment(
        ward_code=candidate.ward_code,
        ward_name=candidate.name,
        district=candidate.district,
        latitude=latitude,
        longitude=longitude,
        distance_m=distance,
        method="nearest_centroid",
        confident=False,
    )


def _ward_fields(ward) -> dict:
    """Project a Ward row onto the fields the matcher needs."""
    return {
        "ward_code": ward.ward_code,
        "name": ward.name,
        "district": ward.district,
        "centroid_latitude": ward.centroid_latitude,
        "centroid_longitude": ward.centroid_longitude,
        "min_latitude": ward.min_latitude,
        "max_latitude": ward.max_latitude,
        "min_longitude": ward.min_longitude,
        "max_longitude": ward.max_longitude,
    }