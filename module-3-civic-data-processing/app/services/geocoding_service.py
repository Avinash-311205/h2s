"""Geocoding abstraction.

Module 3 must never be hard-coded to one geospatial provider. Everything the
pipeline needs is expressed by the :class:`Geocoder` interface; concrete
implementations can wrap government GIS services, OpenStreetMap/Nominatim,
PostGIS-backed gazetteers or national geospatial APIs.

Two implementations ship today:

* :class:`MockGeocoder` - fully offline gazetteer so the module (and the test
  suite) runs without any external service.
* :class:`NominatimGeocoder` - OpenStreetMap forward/reverse geocoding.

:class:`ChainedGeocoder` composes providers with a fallback, which is how the
default configuration behaves so that an external outage degrades gracefully.

Geocoding failures never reject a request: :class:`GeocodingService` converts
any error into a ``UNRESOLVED`` (or ``PARTIAL``) result and records it as a
cleaning-style issue.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from typing import Any, Optional, Sequence

from app.core.config import settings
from app.core.enums import LocationStatus
from app.core.geo import haversine_meters, valid_coordinates
from app.core.logging import get_logger
from app.core.utils import normalize_text

logger = get_logger(__name__)


@dataclass
class GeocodeResult:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    village: Optional[str] = None
    ward: Optional[str] = None
    taluk: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None

    location_status: str = LocationStatus.UNRESOLVED.value
    provider: str = "none"
    confidence: float = 0.0
    resolution_method: Optional[str] = None
    formatted_address: Optional[str] = None
    error: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return self.location_status == LocationStatus.RESOLVED.value

    @property
    def partial(self) -> bool:
        return self.location_status == LocationStatus.PARTIAL.value

    @property
    def has_coordinates(self) -> bool:
        return valid_coordinates(self.latitude, self.longitude)

    @property
    def has_admin(self) -> bool:
        return any((self.village, self.ward, self.taluk, self.district, self.state, self.country))

    @property
    def admin_line(self) -> Optional[str]:
        parts = [self.village, self.ward, self.taluk, self.district, self.state, self.country]
        joined = ", ".join(part for part in parts if part)
        return joined or None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GazetteerEntry:
    name: str
    aliases: Sequence[str] = field(default_factory=tuple)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    village: Optional[str] = None
    ward: Optional[str] = None
    taluk: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    # (min_lat, min_lon, max_lat, max_lon) - used before distance heuristics so
    # that administrative boundaries win over "nearest big city".
    bbox: Optional[tuple[float, float, float, float]] = None

    @property
    def bbox_area(self) -> float:
        if not self.bbox:
            return float("inf")
        min_lat, min_lon, max_lat, max_lon = self.bbox
        return abs((max_lat - min_lat) * (max_lon - min_lon))

    @property
    def has_point(self) -> bool:
        return valid_coordinates(self.latitude, self.longitude)

    @property
    def has_admin(self) -> bool:
        return any((self.village, self.ward, self.taluk, self.district, self.state, self.country))


# ---------------------------------------------------------------------------
# Offline gazetteer used by MockGeocoder
# ---------------------------------------------------------------------------
GAZETTEER: tuple[GazetteerEntry, ...] = (
    # --- India: Tamil Nadu / demo region of the sample payload ------------
    GazetteerEntry(
        name="Chengalpattu",
        aliases=("chengalpattu", "chengalpet", "chengalpattu district"),
        latitude=12.6819,
        longitude=79.9888,
        taluk="Chengalpattu",
        district="Chengalpattu",
        state="Tamil Nadu",
        country="India",
        bbox=(12.00, 79.55, 13.05, 80.35),
    ),
    GazetteerEntry(
        name="Chennai",
        aliases=("chennai", "madras", "chennai district"),
        latitude=13.0827,
        longitude=80.2707,
        district="Chennai",
        state="Tamil Nadu",
        country="India",
        bbox=(13.00, 80.10, 13.23, 80.33),
    ),
    GazetteerEntry(
        name="Coimbatore",
        aliases=("coimbatore", "kovai"),
        latitude=11.0168,
        longitude=76.9558,
        district="Coimbatore",
        state="Tamil Nadu",
        country="India",
    ),
    GazetteerEntry(
        name="Madurai",
        aliases=("madurai",),
        latitude=9.9252,
        longitude=78.1198,
        district="Madurai",
        state="Tamil Nadu",
        country="India",
    ),
    # --- India: other states ---------------------------------------------
    GazetteerEntry(
        name="Bengaluru",
        aliases=("bengaluru", "bangalore"),
        latitude=12.9716,
        longitude=77.5946,
        district="Bengaluru Urban",
        state="Karnataka",
        country="India",
        bbox=(12.75, 77.40, 13.15, 77.80),
    ),
    GazetteerEntry(
        name="Mysuru",
        aliases=("mysuru", "mysore"),
        latitude=12.2958,
        longitude=76.6394,
        district="Mysuru",
        state="Karnataka",
        country="India",
    ),
    GazetteerEntry(
        name="Hyderabad",
        aliases=("hyderabad", "secunderabad"),
        latitude=17.3850,
        longitude=78.4867,
        district="Hyderabad",
        state="Telangana",
        country="India",
        bbox=(17.20, 78.25, 17.60, 78.65),
    ),
    GazetteerEntry(
        name="Vijayawada",
        aliases=("vijayawada", "bezawada"),
        latitude=16.5062,
        longitude=80.6480,
        district="NTR",
        state="Andhra Pradesh",
        country="India",
    ),
    GazetteerEntry(
        name="Mumbai",
        aliases=("mumbai", "bombay"),
        latitude=19.0760,
        longitude=72.8777,
        district="Mumbai",
        state="Maharashtra",
        country="India",
        bbox=(18.88, 72.75, 19.27, 73.05),
    ),
    GazetteerEntry(
        name="Pune",
        aliases=("pune", "poona"),
        latitude=18.5204,
        longitude=73.8567,
        district="Pune",
        state="Maharashtra",
        country="India",
    ),
    GazetteerEntry(
        name="New Delhi",
        aliases=("new delhi", "delhi", "nct of delhi"),
        latitude=28.6139,
        longitude=77.2090,
        district="New Delhi",
        state="Delhi",
        country="India",
        bbox=(28.40, 76.84, 28.90, 77.40),
    ),
    GazetteerEntry(
        name="Lucknow",
        aliases=("lucknow",),
        latitude=26.8467,
        longitude=80.9462,
        district="Lucknow",
        state="Uttar Pradesh",
        country="India",
    ),
    GazetteerEntry(
        name="Kolkata",
        aliases=("kolkata", "calcutta"),
        latitude=22.5726,
        longitude=88.3639,
        district="Kolkata",
        state="West Bengal",
        country="India",
        bbox=(22.40, 88.20, 22.75, 88.50),
    ),
    GazetteerEntry(
        name="Patna",
        aliases=("patna",),
        latitude=25.5941,
        longitude=85.1376,
        district="Patna",
        state="Bihar",
        country="India",
    ),
    GazetteerEntry(
        name="Guwahati",
        aliases=("guwahati", "gauhati"),
        latitude=26.1445,
        longitude=91.7362,
        district="Kamrup Metropolitan",
        state="Assam",
        country="India",
    ),
    GazetteerEntry(
        name="Bhubaneswar",
        aliases=("bhubaneswar", "bhubaneshwar"),
        latitude=20.2961,
        longitude=85.8245,
        district="Khordha",
        state="Odisha",
        country="India",
    ),
    GazetteerEntry(
        name="Kochi",
        aliases=("kochi", "cochin", "ernakulam"),
        latitude=9.9312,
        longitude=76.2673,
        district="Ernakulam",
        state="Kerala",
        country="India",
    ),
    GazetteerEntry(
        name="Thiruvananthapuram",
        aliases=("thiruvananthapuram", "trivandrum"),
        latitude=8.5241,
        longitude=76.9366,
        district="Thiruvananthapuram",
        state="Kerala",
        country="India",
    ),
    # --- BRICS partners (illustrative, keeps the abstraction multi-country) -
    GazetteerEntry(
        name="Sao Paulo",
        aliases=("sao paulo", "são paulo"),
        latitude=-23.5505,
        longitude=-46.6333,
        district="Sao Paulo",
        state="Sao Paulo",
        country="Brazil",
    ),
    GazetteerEntry(
        name="Moscow",
        aliases=("moscow", "москва"),
        latitude=55.7558,
        longitude=37.6173,
        district="Moscow",
        state="Moscow",
        country="Russia",
    ),
    GazetteerEntry(
        name="Beijing",
        aliases=("beijing", "peking", "北京"),
        latitude=39.9042,
        longitude=116.4074,
        district="Beijing",
        state="Beijing",
        country="China",
    ),
    GazetteerEntry(
        name="Johannesburg",
        aliases=("johannesburg", "joburg"),
        latitude=-26.2041,
        longitude=28.0473,
        district="City of Johannesburg",
        state="Gauteng",
        country="South Africa",
    ),
)

# Coarse country bounding boxes used when only coordinates are available.
COUNTRY_BOXES: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("India", (6.5, 68.1, 37.6, 97.4)),
    ("Brazil", (-33.8, -73.9, 5.3, -34.8)),
    ("Russia", (41.2, 19.6, 81.9, 180.0)),
    ("China", (18.0, 73.5, 53.6, 135.1)),
    ("South Africa", (-34.9, 16.3, -22.1, 32.9)),
)


class Geocoder(ABC):
    """Interface every geocoding backend must implement."""

    name: str = "abstract"

    @abstractmethod
    def resolve(
        self,
        location_text: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> GeocodeResult:
        """Return standardized geography for the given input."""
        raise NotImplementedError


class MockGeocoder(Geocoder):
    """Offline gazetteer geocoder - deterministic and network-free."""

    name = "mock"

    def __init__(self, entries: Sequence[GazetteerEntry] = GAZETTEER, nearest_radius_km: Optional[float] = None):
        self.entries = entries
        self.nearest_radius_km = nearest_radius_km or settings.geocoding_nearest_place_radius_km

    # ------------------------------------------------------------------
    def resolve(
        self,
        location_text: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> GeocodeResult:
        has_coords = valid_coordinates(latitude, longitude)
        entry = self._match_text(location_text) if location_text else None

        if entry is not None:
            result = self._from_entry(entry, latitude if has_coords else None, longitude if has_coords else None)
            result.resolution_method = "gazetteer_text+coordinates" if has_coords else "gazetteer_text"
            result.confidence = 0.90 if entry.has_point else 0.60
            return result

        if has_coords:
            return self._from_coordinates(latitude, longitude)

        return GeocodeResult(
            provider=self.name,
            location_status=LocationStatus.UNRESOLVED.value,
            resolution_method="unresolved",
            confidence=0.0,
            error="no coordinates and no matching place name in the gazetteer",
        )

    # ------------------------------------------------------------------
    def _match_text(self, location_text: str) -> Optional[GazetteerEntry]:
        text = normalize_text(location_text).casefold()
        if not text:
            return None

        best: Optional[GazetteerEntry] = None
        best_length = 0
        for entry in self.entries:
            for alias in entry.aliases:
                alias_key = alias.casefold()
                if alias_key and alias_key in text and len(alias_key) > best_length:
                    best = entry
                    best_length = len(alias_key)
        return best

    def _from_coordinates(self, latitude: float, longitude: float) -> GeocodeResult:
        contained = [
            entry
            for entry in self.entries
            if entry.bbox
            and entry.bbox[0] <= latitude <= entry.bbox[2]
            and entry.bbox[1] <= longitude <= entry.bbox[3]
        ]
        if contained:
            entry = min(contained, key=lambda item: item.bbox_area)
            result = self._from_entry(entry, latitude, longitude)
            result.resolution_method = "gazetteer_bbox"
            result.confidence = 0.80
            return result

        nearest, distance = self._nearest(latitude, longitude)
        if nearest is not None and distance is not None and distance <= self.nearest_radius_km * 1000:
            result = self._from_entry(nearest, latitude, longitude)
            result.resolution_method = "nearest_place"
            result.confidence = 0.70
            return result

        country = self._country_from_bbox(latitude, longitude)
        return GeocodeResult(
            latitude=latitude,
            longitude=longitude,
            country=country,
            provider=self.name,
            location_status=LocationStatus.PARTIAL.value,
            resolution_method="coordinates_only",
            confidence=0.40 if country else 0.30,
        )

    def _nearest(self, latitude: float, longitude: float) -> tuple[Optional[GazetteerEntry], Optional[float]]:
        best: Optional[GazetteerEntry] = None
        best_distance: Optional[float] = None
        for entry in self.entries:
            if not entry.has_point:
                continue
            distance = haversine_meters(latitude, longitude, entry.latitude, entry.longitude)
            if distance is None:
                continue
            if best_distance is None or distance < best_distance:
                best, best_distance = entry, distance
        return best, best_distance

    @staticmethod
    def _country_from_bbox(latitude: float, longitude: float) -> Optional[str]:
        for country, (min_lat, min_lon, max_lat, max_lon) in COUNTRY_BOXES:
            if min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon:
                return country
        return None

    def _from_entry(
        self,
        entry: GazetteerEntry,
        latitude: Optional[float],
        longitude: Optional[float],
    ) -> GeocodeResult:
        latitude = latitude if latitude is not None else entry.latitude
        longitude = longitude if longitude is not None else entry.longitude

        if valid_coordinates(latitude, longitude) and (entry.district or entry.state):
            status = LocationStatus.RESOLVED.value
        elif valid_coordinates(latitude, longitude) or entry.has_admin:
            status = LocationStatus.PARTIAL.value
        else:
            status = LocationStatus.UNRESOLVED.value

        return GeocodeResult(
            latitude=latitude,
            longitude=longitude,
            village=entry.village,
            ward=entry.ward,
            taluk=entry.taluk,
            district=entry.district,
            state=entry.state,
            country=entry.country,
            pincode=entry.pincode,
            location_status=status,
            provider=self.name,
        )


class NominatimGeocoder(Geocoder):
    """OpenStreetMap / Nominatim geocoding (forward and reverse)."""

    name = "nominatim"

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None, user_agent: Optional[str] = None):
        self.base_url = (base_url or settings.nominatim_base_url).rstrip("/")
        self.timeout = timeout or settings.geocoding_timeout_seconds
        self.user_agent = user_agent or settings.geocoding_user_agent

    def resolve(
        self,
        location_text: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> GeocodeResult:
        try:
            import httpx
        except ImportError:  # pragma: no cover - httpx is a declared dependency
            return self._failure("httpx is not installed")

        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                if valid_coordinates(latitude, longitude):
                    return self._reverse(client, latitude, longitude)
                if location_text:
                    return self._forward(client, location_text)
        except Exception as exc:  # network, DNS, TLS, timeouts ...
            logger.warning("nominatim_request_failed", extra={"error": str(exc)})
            return self._failure(str(exc))

        return self._failure("insufficient input for geocoding")

    # ------------------------------------------------------------------
    def _reverse(self, client: Any, latitude: float, longitude: float) -> GeocodeResult:
        response = client.get(
            f"{self.base_url}/reverse",
            params={"format": "jsonv2", "lat": latitude, "lon": longitude, "addressdetails": 1},
        )
        response.raise_for_status()
        payload = response.json()
        return self._from_payload(payload, latitude, longitude, "nominatim_reverse")

    def _forward(self, client: Any, location_text: str) -> GeocodeResult:
        response = client.get(
            f"{self.base_url}/search",
            params={"format": "jsonv2", "q": location_text, "addressdetails": 1, "limit": 1},
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return self._failure(f"no geocoding result for '{location_text}'")

        payload = results[0]
        latitude = _to_float(payload.get("lat"))
        longitude = _to_float(payload.get("lon"))
        return self._from_payload(payload, latitude, longitude, "nominatim_forward")

    def _from_payload(
        self,
        payload: dict[str, Any],
        latitude: Optional[float],
        longitude: Optional[float],
        method: str,
    ) -> GeocodeResult:
        address = payload.get("address") or {}

        village = address.get("village") or address.get("hamlet") or address.get("suburb")
        ward = address.get("city_district") or address.get("neighbourhood") or address.get("quarter")
        taluk = address.get("county") or address.get("municipality")
        district = (
            address.get("state_district")
            or address.get("county")
            or address.get("city")
            or address.get("town")
        )
        state = address.get("state") or address.get("region")
        country = address.get("country")

        if valid_coordinates(latitude, longitude) and (district or state):
            status = LocationStatus.RESOLVED.value
        elif valid_coordinates(latitude, longitude) or district or state:
            status = LocationStatus.PARTIAL.value
        else:
            status = LocationStatus.UNRESOLVED.value

        return GeocodeResult(
            latitude=latitude,
            longitude=longitude,
            village=village,
            ward=ward,
            taluk=taluk,
            district=district,
            state=state,
            country=country,
            pincode=address.get("postcode"),
            location_status=status,
            provider=self.name,
            confidence=0.95 if status == LocationStatus.RESOLVED.value else 0.5,
            resolution_method=method,
            formatted_address=payload.get("display_name"),
        )

    def _failure(self, message: str) -> GeocodeResult:
        return GeocodeResult(
            provider=self.name,
            location_status=LocationStatus.UNRESOLVED.value,
            resolution_method="error",
            confidence=0.0,
            error=message,
        )


class ChainedGeocoder(Geocoder):
    """Try providers in order, degrading gracefully on failure."""

    name = "chain"

    def __init__(self, providers: Sequence[Geocoder]):
        if not providers:
            raise ValueError("ChainedGeocoder requires at least one provider")
        self.providers = list(providers)

    def resolve(
        self,
        location_text: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> GeocodeResult:
        fallback: Optional[GeocodeResult] = None
        errors: list[str] = []

        for provider in self.providers:
            try:
                result = provider.resolve(location_text, latitude, longitude)
            except Exception as exc:  # defensive: a provider must never break the pipeline
                errors.append(f"{provider.name}: {exc}")
                logger.warning("geocoder_provider_error", extra={"provider": provider.name, "error": str(exc)})
                continue

            if result.location_status == LocationStatus.RESOLVED.value:
                return result
            if result.error:
                errors.append(f"{provider.name}: {result.error}")
            if fallback is None or (
                result.location_status == LocationStatus.PARTIAL.value
                and fallback.location_status != LocationStatus.PARTIAL.value
            ):
                fallback = result

        if fallback is not None:
            fallback.provider = self.name
            if errors:
                fallback.error = "; ".join(errors)
            return self._preserve_coordinates(fallback, latitude, longitude)

        return GeocodeResult(
            latitude=latitude,
            longitude=longitude,
            provider=self.name,
            location_status=LocationStatus.UNRESOLVED.value,
            resolution_method="error",
            error="; ".join(errors) or "all geocoding providers returned no result",
        )

    @staticmethod
    def _preserve_coordinates(
        result: GeocodeResult, latitude: Optional[float], longitude: Optional[float]
    ) -> GeocodeResult:
        """Never drop the citizen's coordinates just because a provider failed."""
        if not result.has_coordinates and valid_coordinates(latitude, longitude):
            result.latitude, result.longitude = latitude, longitude
        return result


def _build_provider(name: str) -> Optional[Geocoder]:
    key = (name or "").strip().casefold()
    if key == "mock":
        return MockGeocoder()
    if key == "nominatim":
        return NominatimGeocoder()
    if key in ("none", "null", ""):
        return None
    logger.warning("unknown_geocoding_provider", extra={"provider": name})
    return None


@lru_cache
def get_geocoder() -> Geocoder:
    """Factory returning the configured geocoder (cached per process)."""
    primary = _build_provider(settings.geocoding_provider)
    fallback = _build_provider(settings.geocoding_fallback_provider)

    if primary is None:
        primary = MockGeocoder()

    if primary.name == "mock":
        return primary
    if fallback is None or fallback.name == primary.name:
        return primary
    return ChainedGeocoder([primary, fallback])


class GeocodingService:
    """Adds caching, hint merging and error containment around a ``Geocoder``."""

    def __init__(self, geocoder: Optional[Geocoder] = None):
        self.geocoder = geocoder or get_geocoder()
        self._cache: dict[tuple[str, Optional[float], Optional[float]], tuple[float, GeocodeResult]] = {}

    @property
    def provider_name(self) -> str:
        return self.geocoder.name

    def resolve(
        self,
        location_text: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        district_hint: Optional[str] = None,
        state_hint: Optional[str] = None,
        country_hint: Optional[str] = None,
    ) -> tuple[GeocodeResult, list[dict[str, Any]]]:
        """Return ``(result, issues)``. Never raises."""
        issues: list[dict[str, Any]] = []

        # Out-of-range coordinates are never sent to a provider.
        if latitude is not None and not (-90 <= latitude <= 90):
            latitude = None
        if longitude is not None and not (-180 <= longitude <= 180):
            longitude = None

        text_key = normalize_text(location_text or "").casefold()
        cache_key = (text_key, latitude, longitude)

        result = self._cache_get(cache_key)
        if result is None:
            try:
                result = self.geocoder.resolve(location_text, latitude, longitude)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("geocoding_failed", extra={"error": str(exc)})
                result = GeocodeResult(
                    latitude=latitude,
                    longitude=longitude,
                    provider=self.geocoder.name,
                    location_status=LocationStatus.UNRESOLVED.value,
                    resolution_method="error",
                    error=str(exc),
                )
            else:
                if result.error:
                    logger.info(
                        "geocoding_degraded",
                        extra={
                            "provider": result.provider,
                            "status": result.location_status,
                            "error": result.error,
                        },
                    )
            self._cache_set(cache_key, result)

        result = _clone(result)

        # Module 2 hints fill any administrative gap the provider could not cover.
        if not result.district and district_hint:
            result.district = district_hint
        if not result.state and state_hint:
            result.state = state_hint
        if not result.country and country_hint:
            result.country = country_hint
        if not result.latitude and valid_coordinates(latitude, longitude):
            result.latitude, result.longitude = latitude, longitude
        # The provider's own status is advisory; the service applies one
        # consistent rule so results are comparable across backends.
        if result.has_coordinates and (result.district or result.state):
            result.location_status = LocationStatus.RESOLVED.value
        elif result.has_coordinates or result.has_admin:
            result.location_status = LocationStatus.PARTIAL.value
        else:
            result.location_status = LocationStatus.UNRESOLVED.value

        if result.error:
            issues.append(
                {
                    "stage": "geocoding",
                    "code": "GEOCODING_FAILED",
                    "message": result.error,
                    "severity": "warning",
                    "field": "location",
                }
            )

        if result.location_status == LocationStatus.UNRESOLVED.value and not result.error:
            issues.append(
                {
                    "stage": "geocoding",
                    "code": "LOCATION_UNRESOLVED",
                    "message": "location could not be resolved to an administrative unit.",
                    "severity": "warning",
                    "field": "location",
                }
            )

        return result, issues

    # ------------------------------------------------------------------
    def _cache_get(self, key: tuple[str, Optional[float], Optional[float]]) -> Optional[GeocodeResult]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        cached_at, result = entry
        if time.monotonic() - cached_at > settings.geocoding_cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return result

    def _cache_set(self, key: tuple[str, Optional[float], Optional[float]], result: GeocodeResult) -> None:
        if len(self._cache) > 2048:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), result)


def _clone(result: GeocodeResult) -> GeocodeResult:
    """Copy so cached results are never mutated by hint merging."""
    return replace(result)


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
