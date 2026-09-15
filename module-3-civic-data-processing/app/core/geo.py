"""Geospatial helpers independent of any geocoding provider."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Optional

EARTH_RADIUS_METERS = 6_371_008.8

# ~1 degree of latitude in kilometres; used for cheap bounding-box prefilters so
# that the database can prune candidates before haversine is computed.
KM_PER_DEGREE_LAT = 111.32


def valid_latitude(value: Optional[float]) -> bool:
    return value is not None and -90.0 <= value <= 90.0


def valid_longitude(value: Optional[float]) -> bool:
    return value is not None and -180.0 <= value <= 180.0


def valid_coordinates(latitude: Optional[float], longitude: Optional[float]) -> bool:
    return valid_latitude(latitude) and valid_longitude(longitude)


def haversine_meters(
    lat1: Optional[float],
    lon1: Optional[float],
    lat2: Optional[float],
    lon2: Optional[float],
) -> Optional[float]:
    """Great-circle distance in metres, or ``None`` when coordinates are missing."""
    if not valid_coordinates(lat1, lon1) or not valid_coordinates(lat2, lon2):
        return None

    phi1, phi2 = radians(lat1), radians(lat2)  # type: ignore[arg-type]
    d_phi = phi2 - phi1
    d_lambda = radians(lon2 - lon1)  # type: ignore[operator]

    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * asin(sqrt(a))


def bounding_box(
    latitude: float,
    longitude: float,
    radius_meters: float,
) -> tuple[float, float, float, float]:
    """Return ``(min_lat, max_lat, min_lon, max_lon)`` for a metre radius."""
    lat_delta = (radius_meters / 1000.0) / KM_PER_DEGREE_LAT
    min_lat = max(-90.0, latitude - lat_delta)
    max_lat = min(90.0, latitude + lat_delta)

    # Longitude degrees shrink towards the poles; guard against a zero divisor.
    cos_lat = max(cos(radians(latitude)), 0.01)
    lon_delta = (radius_meters / 1000.0) / (KM_PER_DEGREE_LAT * cos_lat)
    min_lon = max(-180.0, longitude - lon_delta)
    max_lon = min(180.0, longitude + lon_delta)

    return min_lat, max_lat, min_lon, max_lon


def build_point_wkt(longitude: Optional[float], latitude: Optional[float]) -> Optional[str]:
    """WKT representation stored alongside the numeric coordinates."""
    if not valid_coordinates(latitude, longitude):
        return None
    return f"POINT({longitude} {latitude})"
