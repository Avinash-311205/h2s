"""Geometry: point-to-ward assignment."""

from __future__ import annotations

import pytest

from app.core.utils import bounding_box, haversine_metres, is_valid_pincode
from app.services import geo_service
from tests.conftest import make_ward


def test_point_inside_bounding_box_is_confident():
    wards = [make_ward("CHN-01", lat=13.0827, lon=80.2707, span=0.05)]

    assignment = geo_service.assign_to_ward(13.08, 80.27, wards)

    assert assignment.ward_code == "CHN-01"
    assert assignment.method == "bounding_box"
    assert assignment.confident is True
    assert assignment.distance_m is not None and assignment.distance_m < 1_000


def test_point_outside_every_box_falls_back_to_nearest_centroid():
    wards = [make_ward("CHN-01", lat=13.0827, lon=80.2707, span=0.01)]

    # ~2.2 km north of the centroid: outside the box, inside the 25 km tolerance.
    assignment = geo_service.assign_to_ward(13.1027, 80.2707, wards)

    assert assignment.ward_code == "CHN-01"
    assert assignment.method == "nearest_centroid"
    assert assignment.confident is False
    assert 1_500 < assignment.distance_m < 3_000


def test_point_far_from_any_ward_is_unassigned():
    """A distant point must not be snapped to a wrong ward."""
    wards = [make_ward("CHN-01", lat=13.0827, lon=80.2707, span=0.05)]

    assignment = geo_service.assign_to_ward(20.0, 78.0, wards)

    assert assignment.ward_code is None
    assert assignment.method == "out_of_range"
    assert assignment.confident is False


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(None, 80.27), (13.08, None), (91.0, 80.27), (13.08, 181.0), (float("nan"), 80.27)],
)
def test_invalid_coordinates_are_rejected(latitude, longitude):
    assignment = geo_service.assign_to_ward(latitude, longitude, [make_ward()])

    assert assignment.ward_code is None
    assert assignment.method == "invalid_coordinates"
    assert assignment.confident is False


def test_empty_ward_layer_reports_empty_not_crash():
    assignment = geo_service.assign_to_ward(13.08, 80.27, [])

    assert assignment.ward_code is None
    assert assignment.method == "empty_ward_layer"


def test_ward_without_bounds_matches_anywhere():
    """Open bounds mean "no constraint", so an unbounded ward claims the point.

    Distance to its centroid is still reported, so a caller can tell an unbounded
    match from a genuine inside-the-box hit.
    """
    ward = make_ward("CHN-09", lat=13.0827, lon=80.2707)
    ward.min_latitude = ward.max_latitude = None
    ward.min_longitude = ward.max_longitude = None

    assignment = geo_service.assign_to_ward(13.0830, 80.2708, [ward])

    assert assignment.ward_code == "CHN-09"
    assert assignment.distance_m is not None and assignment.distance_m < 100


def test_haversine_distance_is_plausible():
    # Chennai to Coimbatore is ~430 km as the crow flies (~505 km by road).
    distance = haversine_metres(13.0827, 80.2707, 11.0168, 76.9558)

    assert 420_000 < distance < 440_000


def test_bounding_box_widens_with_latitude():
    _, _, equator_min, equator_max = bounding_box(0.0, 0.0, 10_000)
    _, _, high_min, high_max = bounding_box(60.0, 0.0, 10_000)

    assert (equator_max - equator_min) < (high_max - high_min)


def test_bounding_box_never_inverts_at_the_poles():
    min_lat, max_lat, min_lon, max_lon = bounding_box(89.99, 10.0, 25_000)

    assert min_lat <= max_lat
    assert min_lon <= max_lon


def test_pincode_validation():
    assert is_valid_pincode("600001") is True
    assert is_valid_pincode("60000") is False
    assert is_valid_pincode("60000a") is False
    assert is_valid_pincode(None) is False