"""Hotspot detection: where is demand concentrated, and by how much?

Three decisions shape this implementation:

**Normalise before ranking.** Complaint counts alone just rank wards by size, so
intensity is reported per 1,000 residents. A dense ward of 8,000 people with 400
complaints is a worse situation than a ward of 32,000 with 900.

**Cluster by geography, not by rank.** Neighbouring wards are joined into a
cluster with a union-find over centroid distance, so a hotspot is a place a
policymaker can be sent, not an arbitrary top-N list. Dependency-free
deliberately: no Shapely, no clustering library, and a result that can be checked
by eye against a map.

**Score against the city, not an absolute constant.** A ward's intensity is
expressed as standard deviations above the city-wide mean, and tiers are cut at
percentiles. Both keep their meaning as the number of wards changes, which a
hard-coded "more than 500 complaints" threshold does not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from app.core.config import settings
from app.core.enums import HotspotTier
from app.core.utils import clamp, mean, percentile, pct, safe_div, z_score

# Tier cut-offs as percentile ranks of cluster mean intensity.
_TIER_CUTS = {
    HotspotTier.CRITICAL: 0.90,
    HotspotTier.HIGH: 0.75,
    HotspotTier.MODERATE: 0.50,
}


@dataclass
class WardSignal:
    """One ward's demand, already normalised for size."""

    ward_code: str
    name: str
    district: str
    latitude: float
    longitude: float
    population: int
    complaints: int = 0
    critical_complaints: int = 0
    peak_severity: float = 0.0
    sectors: list[str] = field(default_factory=list)

    @property
    def intensity(self) -> float:
        """Complaints per 1,000 residents."""
        denominator = max(self.population, 1) / settings.population_per_unit
        return round(safe_div(self.complaints, denominator), 4)


@dataclass
class HotspotResult:
    """A detected cluster of wards."""

    district: str
    latitude: float
    longitude: float
    ward_codes: list[str]
    sectors: list[str]
    population: int
    total_complaints: int
    critical_complaints: int
    mean_intensity: float
    peak_intensity: float
    peak_severity: float
    intensity_z_score: float
    tier: str
    window_days: int
    ward_count: int = 0

    def as_dict(self) -> dict:
        return {
            "district": self.district,
            "latitude": round(self.latitude, 6),
            "longitude": round(self.longitude, 6),
            "ward_codes": sorted(self.ward_codes),
            "sectors": sorted(self.sectors),
            "population": self.population,
            "total_complaints": self.total_complaints,
            "critical_complaints": self.critical_complaints,
            "mean_intensity": round(self.mean_intensity, 4),
            "peak_intensity": round(self.peak_intensity, 4),
            "peak_severity": round(self.peak_severity, 4),
            "intensity_z_score": round(self.intensity_z_score, 4),
            "tier": self.tier,
            "window_days": self.window_days,
            "ward_count": self.ward_count or len(self.ward_codes),
        }


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (mean earth radius)."""
    radius = 6_371_008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def cluster_wards(
    wards: Sequence[WardSignal], radius_m: Optional[float] = None
) -> list[list[WardSignal]]:
    """Group wards into geographic clusters using union-find.

    Two wards join the same cluster when their centroids are within
    ``radius_m`` *of each other*. Transitive closure is intended: a chain of
    wards each within the radius forms one cluster, which matches how a
    contiguous urban area behaves.

    Complexity is O(n^2) in the number of wards. At ward granularity that is
    fine into the low thousands; beyond that a spatial index (rtree, PostGIS)
    becomes worthwhile, and the interface does not change.
    """
    radius = radius_m if radius_m is not None else settings.cluster_radius_m
    parent: dict[str, str] = {ward.ward_code: ward.ward_code for ward in wards}

    def find(code: str) -> str:
        while parent[code] != code:
            parent[code] = parent[parent[code]]  # path halving
            code = parent[code]
        return code

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    items = list(wards)
    for index, left in enumerate(items):
        for right in items[index + 1:]:
            if haversine_m(left.latitude, left.longitude, right.latitude, right.longitude) <= radius:
                union(left.ward_code, right.ward_code)

    clusters: dict[str, list[WardSignal]] = {}
    for ward in items:
        clusters.setdefault(find(ward.ward_code), []).append(ward)
    return list(clusters.values())


def tier_for(rank_fraction: float) -> str:
    """Tier from the cluster's percentile rank among all clusters."""
    for tier, cut in _TIER_CUTS.items():
        if rank_fraction >= cut:
            return tier.value
    return HotspotTier.NORMAL.value


def build_hotspot(
    members: Sequence[WardSignal], ward_intensities: Sequence[float], *, window_days: int = 30
) -> HotspotResult:
    """Summarise one cluster into a hotspot record."""
    populations = [ward.population for ward in members]
    intensities = [ward.intensity for ward in members]
    total_population = sum(populations)

    # Complaints per 1,000 residents across the cluster as a whole. Using the
    # cluster total rather than the mean of ward rates stops one huge ward from
    # masking several small loud ones.
    cluster_rate = safe_div(
        sum(ward.complaints for ward in members),
        max(total_population, 1) / settings.population_per_unit,
    )

    return HotspotResult(
        district=members[0].district,
        latitude=mean([ward.latitude for ward in members]),
        longitude=mean([ward.longitude for ward in members]),
        ward_codes=[ward.ward_code for ward in members],
        sectors=sorted({sector for ward in members for sector in ward.sectors}),
        population=total_population,
        total_complaints=sum(ward.complaints for ward in members),
        critical_complaints=sum(ward.critical_complaints for ward in members),
        mean_intensity=round(cluster_rate, 4),
        peak_intensity=round(max(intensities), 4),
        peak_severity=round(max(ward.peak_severity for ward in members), 4),
        intensity_z_score=z_score(cluster_rate, ward_intensities),
        tier=HotspotTier.NORMAL.value,
        window_days=window_days,
        ward_count=len(members),
    )


def detect_hotspots(
    wards: Sequence[WardSignal],
    *,
    window_days: int = 30,
    min_complaints: int = 1,
    radius_m: Optional[float] = None,
) -> list[HotspotResult]:
    """Detect and tier every hotspot in a ward set.

    Clusters with no complaints at all are dropped: an empty area is not a
    hotspot, and including it would drag the percentile cut-offs down for
    everyone else.

    Returns clusters ranked worst-first by mean intensity.
    """
    if not wards:
        return []

    clusters = cluster_wards(wards, radius_m=radius_m)
    ward_intensities = [ward.intensity for ward in wards]

    results: list[HotspotResult] = []
    for members in clusters:
        complaints = sum(ward.complaints for ward in members)
        if complaints < min_complaints:
            continue
        results.append(build_hotspot(members, ward_intensities, window_days=window_days))

    if not results:
        return []

    rates = [hotspot.mean_intensity for hotspot in results]
    for hotspot in results:
        rank = _rank_fraction(hotspot.mean_intensity, rates)
        hotspot.tier = tier_for(rank)

    results.sort(
        key=lambda hotspot: (-hotspot.mean_intensity, -hotspot.critical_complaints, hotspot.district)
    )
    return results


def _rank_fraction(value: float, population: Sequence[float]) -> float:
    """Fraction of the population at or below ``value`` (0-1)."""
    if not population:
        return 0.0
    return round(sum(1 for other in population if other <= value) / len(population), 4)


def summarise_tiers(hotspots: Sequence[HotspotResult]) -> dict[str, int]:
    """Count clusters per tier, always reporting all four keys."""
    counts = {tier.value: 0 for tier in HotspotTier}
    for hotspot in hotspots:
        counts[hotspot.tier] += 1
    return counts


def district_rollup(hotspots: Sequence[HotspotResult]) -> list[dict]:
    """Aggregate clusters by district, worst intensity first."""
    grouped: dict[str, list[HotspotResult]] = {}
    for hotspot in hotspots:
        grouped.setdefault(hotspot.district, []).append(hotspot)

    rows = []
    for district, group in grouped.items():
        complaints = sum(hotspot.total_complaints for hotspot in group)
        population = sum(hotspot.population for hotspot in group)
        rows.append(
            {
                "district": district,
                "hotspot_count": len(group),
                "total_complaints": complaints,
                "population": population,
                "intensity": round(
                    safe_div(complaints, max(population, 1) / settings.population_per_unit), 4
                ),
                "critical_hotspots": sum(
                    1 for hotspot in group if hotspot.tier == HotspotTier.CRITICAL.value
                ),
                "complaint_share": 0.0,
            }
        )

    total_complaints = sum(row["total_complaints"] for row in rows) or 1
    for row in rows:
        row["complaint_share"] = round(clamp(pct(row["total_complaints"], total_complaints), 0.0, 1.0), 4)
    rows.sort(key=lambda row: -row["intensity"])
    return rows


def coverage_note(hotspots: Sequence[HotspotResult], wards: Sequence[WardSignal]) -> dict:
    """How much of the ward population the hotspots account for.

    A hotspot list covering 90% of the city is not a hotspot list, it is a
    city-wide problem, and this is what makes that distinction visible.
    """
    total_population = sum(ward.population for ward in wards)
    covered = sum(hotspot.population for hotspot in hotspots)
    return {
        "ward_count": len(wards),
        "hotspot_count": len(hotspots),
        "covered_population": covered,
        "covered_share": round(clamp(pct(covered, total_population), 0.0, 1.0), 4),
        "total_population": total_population,
        "median_cluster_intensity": round(median_intensity(hotspots), 4),
    }


def median_intensity(hotspots: Sequence[HotspotResult]) -> float:
    """Median cluster intensity (kept separate for readability in reports)."""
    if not hotspots:
        return 0.0
    ordered = sorted(hotspot.mean_intensity for hotspot in hotspots)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def hotspot_intensity_band(hotspots: Sequence[HotspotResult], fraction: float = 0.9) -> float:
    """Intensity percentile across clusters, for dashboards and thresholds."""
    return round(percentile([hotspot.mean_intensity for hotspot in hotspots], fraction), 4)


def window_bounds(window_days: int, *, reference: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """``(start, end)`` of a trailing window, for stamping hotspot rows."""
    from app.core.utils import utcnow, window_start

    end = reference or utcnow()
    return window_start(window_days, reference=end), end