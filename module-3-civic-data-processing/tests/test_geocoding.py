"""Geocoding: provider abstraction, mock gazetteer and graceful degradation."""

from __future__ import annotations

from app.core.enums import LocationStatus
from app.services.geocoding_service import (
    ChainedGeocoder,
    GeocodeResult,
    Geocoder,
    GeocodingService,
    MockGeocoder,
    get_geocoder,
)


class ExplodingGeocoder(Geocoder):
    name = "exploding"

    def resolve(self, location_text=None, latitude=None, longitude=None) -> GeocodeResult:
        raise RuntimeError("provider exploded")


class UnresolvedGeocoder(Geocoder):
    name = "unresolved"

    def resolve(self, location_text=None, latitude=None, longitude=None) -> GeocodeResult:
        return GeocodeResult(provider=self.name, location_status=LocationStatus.UNRESOLVED.value)


mock = MockGeocoder()


def test_text_location_is_resolved_to_admin_hierarchy():
    result = mock.resolve(location_text="Chengalpattu, Tamil Nadu")

    assert result.location_status == LocationStatus.RESOLVED.value
    assert result.district == "Chengalpattu"
    assert result.state == "Tamil Nadu"
    assert result.country == "India"
    assert result.has_coordinates


def test_coordinates_are_resolved_to_admin_hierarchy():
    result = mock.resolve(latitude=12.9249, longitude=80.1000)

    assert result.location_status == LocationStatus.RESOLVED.value
    assert result.district == "Chengalpattu"
    assert result.state == "Tamil Nadu"
    assert result.country == "India"
    assert result.resolution_method == "gazetteer_bbox"


def test_unknown_place_without_coordinates_is_unresolved():
    result = mock.resolve(location_text="Nowhereville")

    assert result.location_status == LocationStatus.UNRESOLVED.value
    assert result.district is None
    assert result.error


def test_coordinates_outside_the_gazetteer_are_partially_resolved():
    result = mock.resolve(latitude=-33.9249, longitude=18.4241)

    assert result.location_status == LocationStatus.PARTIAL.value
    assert result.country == "South Africa"
    assert result.latitude == -33.9249


def test_multi_country_gazetteer_support():
    cases = {
        "São Paulo": ("Sao Paulo", "Brazil"),
        "Moscow": ("Moscow", "Russia"),
        "Beijing": ("Beijing", "China"),
        "Johannesburg": ("City of Johannesburg", "South Africa"),
    }

    for text, (district, country) in cases.items():
        result = mock.resolve(location_text=text)
        assert result.district == district, text
        assert result.country == country, text


def test_service_preserves_original_location_when_geocoding_fails():
    service = GeocodingService(ExplodingGeocoder())

    result, issues = service.resolve(location_text="Some place", latitude=12.9249, longitude=80.1000)

    # The request is not rejected: coordinates survive and the failure is logged.
    assert result.location_status == LocationStatus.PARTIAL.value
    assert result.latitude == 12.9249
    assert result.longitude == 80.1
    assert any(issue["code"] == "GEOCODING_FAILED" for issue in issues)


def test_module_2_hints_fill_missing_administrative_levels():
    service = GeocodingService(ExplodingGeocoder())

    result, _ = service.resolve(
        location_text="Some place",
        latitude=12.9249,
        longitude=80.1000,
        state_hint="Tamil Nadu",
        country_hint="India",
    )

    assert result.state == "Tamil Nadu"
    assert result.country == "India"
    assert result.location_status != LocationStatus.UNRESOLVED.value


def test_chained_geocoder_falls_back_to_the_next_provider():
    chain = ChainedGeocoder([ExplodingGeocoder(), MockGeocoder()])

    result = chain.resolve(location_text="Chennai, Tamil Nadu")

    assert result.location_status == LocationStatus.RESOLVED.value
    assert result.district == "Chennai"


def test_chained_geocoder_degrades_when_every_provider_fails():
    chain = ChainedGeocoder([ExplodingGeocoder(), UnresolvedGeocoder()])

    result = chain.resolve(location_text="Anything", latitude=1.0, longitude=2.0)

    assert result.location_status == LocationStatus.UNRESOLVED.value
    assert result.latitude == 1.0


def test_invalid_coordinates_are_never_sent_to_the_provider():
    service = GeocodingService(MockGeocoder())

    result, _ = service.resolve(latitude=999, longitude=-999, district_hint="Chengalpattu")

    assert result.location_status == LocationStatus.PARTIAL.value
    assert result.latitude is None
    assert result.district == "Chengalpattu"


def test_results_are_cached_per_provider_instance():
    service = GeocodingService(MockGeocoder())

    first, _ = service.resolve(location_text="Chennai")
    second, _ = service.resolve(location_text="Chennai")

    assert first.district == second.district
    assert len(service._cache) == 1


def test_default_geocoder_factory_returns_configured_provider():
    assert get_geocoder().name in {"mock", "nominatim", "chain"}
