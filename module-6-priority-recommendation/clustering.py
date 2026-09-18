"""
clustering.py -- DBSCAN grouping of citizen requests into geographic hotspots.

DBSCAN (Density-Based Spatial Clustering of Applications with Noise) is chosen
because it needs no pre-specified number of clusters, handles irregular
shapes well, and robustly labels sparse/noisy entries (single stray complaints)
as noise rather than forcing them into a cluster.

Distances between (latitude, longitude) points are measured with the
*hausdorff-style* great-circle (haversine) distance in metres, so clustering is
geographically meaningful at the scale of a neighbourhood/ward.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sklearn.cluster import DBSCAN


# ---------------------------------------------------------------------------
# DBSCAN hyper-parameters (explicable, tunable constants).
# ---------------------------------------------------------------------------

# EPSILON_METRES -- Maximum distance (in metres) between two requests for
#                   them to be considered neighbours. 300 m keeps a hotspot
#                   tightly scoped to roughly a street/block scale.
EPSILON_METRES: float = 300.0

# MIN_SAMPLES -- Minimum number of requests required to form a cluster.
#                A single complaint is noise, not a hotspot; 3 requests give a
#                meaningful signal while staying sensitive to early warnings.
MIN_SAMPLES: int = 3


# ---------------------------------------------------------------------------
# Region geofencing (mock): assign each centroid to a named region so the
# scoring module can look up population density. Coarse bounding boxes only --
# replace with real ward polygons for production.
# ---------------------------------------------------------------------------

# Each entry is (region_name, (min_lat, max_lat, min_lng, max_lng)).
# Roughly models a compact urban area's neighbourhoods (baseline ~12.97 N, 77.6 E).
REGION_BOUNDS: dict = {
    "downtown": (12.975, 13.005, 77.600, 77.630),
    "old_city": (12.955, 12.975, 77.570, 77.600),
    "industrial_zone": (13.005, 13.035, 77.620, 77.650),
    "residential_east": (12.975, 13.005, 77.630, 77.660),
    "residential_west": (12.955, 12.985, 77.545, 77.570),
    "riverfront": (12.940, 12.955, 77.570, 77.600),
    "suburb_north": (13.020, 13.060, 77.560, 77.610),
    "suburb_south": (12.920, 12.955, 77.560, 77.600),
    "market_district": (12.960, 12.975, 77.600, 77.620),
}

EARTH_RADIUS_M: float = 6_371_000.0


# ---------------------------------------------------------------------------
# Haversine distance helpers.
# ---------------------------------------------------------------------------


def haversine_distance_m(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    """Great-circle distance in metres between two lat/lng points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def pair_haversine_matrix(lats: np.ndarray, lngs: np.ndarray) -> np.ndarray:
    """Full pairwise distance matrix (metres) for a set of coordinates."""
    n = len(lats)
    dists = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_distance_m(lats[i], lngs[i], lats[j], lngs[j])
            dists[i, j] = d
            dists[j, i] = d
    return dists


# ---------------------------------------------------------------------------
# Region helper.
# ---------------------------------------------------------------------------


def location_label(lat: float, lng: float) -> str:
    """Return the region name containing (lat, lng), or 'unknown'."""
    for name, (min_lat, max_lat, min_lng, max_lng) in REGION_BOUNDS.items():
        if min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
            return name
    return "unknown"


# ---------------------------------------------------------------------------
# Data containers.
# ---------------------------------------------------------------------------


@dataclass
class CitizenRequest:
    """A single row from the citizen_requests DB `requests` table."""

    id: int
    category: str
    description: str
    latitude: float
    longitude: float
    severity: float
    language: str
    status: str
    created_at: datetime

    @property
    def days_open(self) -> float:
        """Whole days the request has been unresolved (as of today)."""
        delta = (datetime.now(tz=self.created_at.tzinfo) - self.created_at)
        return max(0.0, delta.total_seconds() / 86400.0)


@dataclass
class Hotspot:
    """A DBSCAN cluster with the aggregate metrics used for scoring."""

    id: int
    category: str
    centroid_lat: float
    centroid_lng: float
    request_ids: list = field(default_factory=list)
    request_count: int = 0
    avg_severity: float = 0.0
    avg_days_open: float = 0.0
    region: str = "unknown"
    sample_texts: list = field(default_factory=list)
    priority_score: float = 0.0
    priority_factors: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Clustering engine.
# ---------------------------------------------------------------------------


def cluster_requests(
    requests: list, eps: float = EPSILON_METRES, min_samples: int = MIN_SAMPLES
) -> list:
    """Group requests into hotspots with DBSCAN on haversine distance.

    Clusters are computed per category, so a pothole cluster never merges with
    a water-leak cluster even if they overlap geographically -- policy action
    differs by issue type.

    Args:
        requests: list of CitizenRequest to cluster.
        eps: DBSCAN neighbourhood radius in metres.
        min_samples: minimum points to form a cluster.

    Returns:
        A list of Hotspot dataclasses (one per category-cluster).
    """
    # Group request indices by category for per-category clustering.
    by_category: dict = {}
    for idx, req in enumerate(requests):
        by_category.setdefault(req.category, []).append(idx)

    hotspots: list = []
    counter = 0

    for category, indices in by_category.items():
        cat_requests = [requests[i] for i in indices]

        lats = np.array([r.latitude for r in cat_requests], dtype=np.float64)
        lngs = np.array([r.longitude for r in cat_requests], dtype=np.float64)

        if len(cat_requests) < min_samples:
            # Not enough points to form a hotspot; skip (keeps output clean).
            continue

        # Distance matrix in metres -> DBSCAN with eps in metres.
        dists = pair_haversine_matrix(lats, lngs)
        clusters = DBSCAN(
            metric="precomputed", eps=eps, min_samples=min_samples
        ).fit(dists)
        labels = clusters.labels_

        # -1 == noise; every other label is one hotspot.
        for label in sorted(set(labels)):
            if label == -1:
                continue

            member_ids = [ii for ii, lab in enumerate(labels) if lab == label]

            # Centroid as the mean of member coordinates (map visually sensible).
            clat = float(np.mean(lats[member_ids]))
            clng = float(np.mean(lngs[member_ids]))

            counters = [cat_requests[ii] for ii in member_ids]

            avg_sev = float(np.mean([c.severity for c in counters]))
            avg_days = float(np.mean([c.days_open for c in counters]))
            region = location_label(clat, clng)

            _priority_score, _factors = compute_hotspot_score(
                len(counters), avg_sev, region, avg_days
            )

            hotspots.append(
                Hotspot(
                    id=counter,
                    category=category,
                    centroid_lat=round(clat, 6),
                    centroid_lng=round(clng, 6),
                    request_ids=[c.id for c in counters],
                    request_count=len(counters),
                    avg_severity=round(avg_sev, 2),
                    avg_days_open=round(avg_days, 2),
                    region=region,
                    # Keep up to 3 complaints as human-readable evidence.
                    sample_texts=[c.description for c in counters[:3]],
                    priority_score=_priority_score,
                    priority_factors=_factors,
                )
            )
            counter += 1

    # Rank hottest first so downstream consumers see the most urgent first.
    hotspots.sort(key=lambda h: h.priority_score, reverse=True)
    return hotspots


def compute_hotspot_score(
    request_count: int, avg_severity: float, region: str, days_open: float
) -> tuple[float, dict]:
    """Thin wrapper around scoring.compute_priority_score.

    Kept separate so clustering.py never imports scoring at the top level
    (avoids any import-order surprises) while giving one obvious call site
    for the scoring pipeline.
    """
    from scoring import compute_priority_score

    return compute_priority_score(
        request_count=request_count,
        avg_severity=avg_severity,
        region_name=region,
        days_open=days_open,
    )