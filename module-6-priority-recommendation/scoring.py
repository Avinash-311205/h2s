"""
scoring.py -- Transparent, auditable priority scoring for civic hotspots.

This module computes a Priority Score (0-100) for each geographic hotspot
identified by DBSCAN clustering. The score is a *weighted sum* of four,
independently interpretable factors:

    priority_score = w1 * norm_count     (+ volume of complaints)
                   + w2 * norm_severity  (+ average reported severity)
                   + w3 * norm_density   (+ estimated population density)
                   + w4 * norm_days_open (+ how long issues went unresolved)

The design goal is transparency: every weight and every normalisation step
is a named constant below, so judges and policymakers can audit exactly how
a score was derived -- no black box.

Scoring recipe (per hotspot):
  1. Normalise each raw factor to a 0-100 scale using a reference maximum
     (or, for severity, the raw 0-5 scale blown up to 0-100).
  2. Multiply each normalised factor by its configured weight.
  3. Sum the weighted contributions; the result is already on a 0-100 scale
     because the weights themselves sum to 1.0.
"""

import math

# ---------------------------------------------------------------------------
# Configurable weights for each factor.
# Each weight represents the *relative importance* of that factor when a
# policymaker decides which hotspot to fund first. Weights must sum to 1.0
# so the final Priority Score always lands on the shared 0-100 scale.
# ---------------------------------------------------------------------------

# W_VOLUME   -- Importance of "how many citizens are complaining here".
#               Crowded complaints = more affected citizens = higher need.
#               Highest weight: volume is the strongest signal of impact.
W_VOLUME: float = 0.35

# W_SEVERITY -- Importance of "how severe is the reported problem".
#               Severity column is assumed to be 1 (minor) .. 5 (critical).
#               Severe issues (e.g. open manholes, broken water lines)
#               demand faster action than cosmetic ones.
W_SEVERITY: float = 0.30

# W_DENSITY  -- Importance of "how densely populated is the region".
#               A pothole affects 50,000 people downtown but only 5,000
#               in a suburb -- density scales the real-world impact.
#               NOTE: density comes from a static MOCK lookup until real
#               census data is wired in (see POPULATION_DENSITY below).
W_DENSITY: float = 0.15

# W_DAYS_OPEN -- Importance of "how many days the issue has been unresolved".
#               Older, ignored complaints signal service failure and
#               erode public trust, so they deserve a bump.
W_DAYS_OPEN: float = 0.20

# Guard: assert weights sum to 1 so the 0-100 scale is preserved.
assert abs(W_VOLUME + W_SEVERITY + W_DENSITY + W_DAYS_OPEN - 1.0) < 1e-9, (
    "Priority score weights must sum to 1.0; got "
    f"{W_VOLUME + W_SEVERITY + W_DENSITY + W_DAYS_OPEN}"
)


# ---------------------------------------------------------------------------
# Normalisation reference maxima.
# These define what we consider the "worst case" for each factor, against
# which every hotspot's raw value is scaled to a 0-100 figure.
# ---------------------------------------------------------------------------

# REF_MAX_COUNT -- A hotspot with this many complaints is treated as the
#                  absolute worst case for volume (raw count -> 100).
#                  Chosen as a round, explainable ceiling; a single ward
#                  receiving hundreds of complaints is clearly critical.
REF_MAX_COUNT: int = 100

# REF_MAX_DAYS_OPEN -- A complaint open this many days is treated as the
#                      absolute worst case for delay.
#                      ~3 months of an ignored civic issue is extreme.
REF_MAX_DAYS_OPEN: int = 90

# SEVERITY_SCALE -- Assumed maximum of the `severity` column (1..5).
#                   Used to blow the 1-5 severity up to the 0-100 scale.
SEVERITY_SCALE: float = 5.0

# DENSITY_SCALE -- Assumed maximum of the (mock) population-density lookup,
#                  i.e. people per square kilometre.
DENSITY_SCALE: float = 30_000.0


# ---------------------------------------------------------------------------
# Mock static population-density lookup, keyed by region name.
#
# Until a real census / LGD (Local Government Directory) dataset is wired in,
# this dictionary plays the role of "region -> people per sq. km". It is
# intentionally a tiny, readable stand-in so the scoring pipeline can run
# end-to-end today. Swap this for a real lookup to put it into production.
# ---------------------------------------------------------------------------
POPULATION_DENSITY: dict = {
    "downtown": 25000,
    "old_city": 22000,
    "industrial_zone": 9000,
    "residential_east": 14000,
    "residential_west": 12000,
    "riverfront": 8000,
    "suburb_north": 4000,
    "suburb_south": 3500,
    "market_district": 20000,
    "unknown": 8000,
}


def density_for_region(region_name: str) -> float:
    """Return population density (people / sq km) for a region name.

    Falls back to a conservative default so hotspots without a known region
    still score sensibly instead of crashing the pipeline.

    Args:
        region_name: map a hotspot's centroid to a named region (see
            clustering.location_label()). Coarse geofencing is done there.

    Returns:
        Density in people per square kilometre (float).
    """
    key = (region_name or "unknown").strip().lower()
    return float(POPULATION_DENSITY.get(key, POPULATION_DENSITY["unknown"]))


# ---------------------------------------------------------------------------
# Normalisation helpers -- each maps a raw factor to a 0-100 scale.
# ---------------------------------------------------------------------------


def _cap(value: float, maximum: float) -> float:
    """Clip *value* into the [0.0, maximum] range."""
    return max(0.0, min(float(value), float(maximum)))


def normalise_count(request_count: int) -> float:
    """Volume of complaints as a 0-100 score (count relative to REF_MAX_COUNT)."""
    return _cap(request_count, REF_MAX_COUNT) / REF_MAX_COUNT * 100.0


def normalise_severity(avg_severity: float) -> float:
    """Average 1-5 severity scaled to 0-100 (severity 5 -> 100)."""
    return _cap(avg_severity, SEVERITY_SCALE) / SEVERITY_SCALE * 100.0


def normalise_density(avg_density: float) -> float:
    """Population density (people/sq.km) scaled to 0-100."""
    return _cap(avg_density, DENSITY_SCALE) / DENSITY_SCALE * 100.0


def normalise_days_open(days_open: float) -> float:
    """Average unresolved time scaled to 0-100 (>= REF_MAX_DAYS_OPEN -> 100)."""
    return _cap(days_open, REF_MAX_DAYS_OPEN) / REF_MAX_DAYS_OPEN * 100.0


# ---------------------------------------------------------------------------
# Public scoring entry point.
# ---------------------------------------------------------------------------


def compute_priority_score(
    request_count: int,
    avg_severity: float,
    region_name: str,
    days_open: float,
) -> tuple[float, dict]:
    """Compute the 0-100 Priority Score for a single hotspot.

    The score is the weighted sum of each factor after each factor has been
    normalised to the same 0-100 scale:

        normalised factor x weight  ->  contributes at most "weight"*100 points
        sum over all 4 factors      ->  contributes at most 100 points

    Args:
        request_count: number of citizen requests in the hotspot.
        avg_severity: mean severity across those requests (1-5).
        region_name: name used to look up mock population density.
        days_open: average days the requests have been unresolved.

    Returns:
        A tuple of (priority_score, breakdown) where breakdown is a dict that
        exposes the raw factor, its normalised value, and its weighted
        contribution -- so the final number can be fully explained line by line.
    """
    avg_density = density_for_region(region_name)

    # Normalise each raw factor independently to 0-100.
    n_count = normalise_count(request_count)
    n_severity = normalise_severity(avg_severity)
    n_density = normalise_density(avg_density)
    n_days = normalise_days_open(days_open)

    # Weighted contributions (each adds at most 100 * weight points).
    factored = {
        "volume": {
            "raw": request_count,
            "normalised": round(n_count, 2),
            "weight": W_VOLUME,
            "contribution": round(n_count * W_VOLUME, 2),
        },
        "severity": {
            "raw": round(float(avg_severity), 2),
            "normalised": round(n_severity, 2),
            "weight": W_SEVERITY,
            "contribution": round(n_severity * W_SEVERITY, 2),
        },
        "population_density": {
            "raw": round(float(avg_density), 2),
            "normalised": round(n_density, 2),
            "weight": W_DENSITY,
            "contribution": round(n_density * W_DENSITY, 2),
        },
        "days_open": {
            "raw": round(float(days_open), 2),
            "normalised": round(n_days, 2),
            "weight": W_DAYS_OPEN,
            "contribution": round(n_days * W_DAYS_OPEN, 2),
        },
    }

    # Total = weighted sum of normalised factors -> 0-100 scale.
    priority_score = (
        n_count * W_VOLUME
        + n_severity * W_SEVERITY
        + n_density * W_DENSITY
        + n_days * W_DAYS_OPEN
    )

    # Guard against float drifts (e.g. NaN weights) pushing us off-scale.
    priority_score = max(0.0, min(100.0, round(priority_score, 2)))

    return priority_score, factored


# ---------------------------------------------------------------------------
# Plain-language recommendation generation (template-based, no LLM).
# ---------------------------------------------------------------------------

# Intensifier chosen from the score so recommendations read naturally.
NOT_TEMPLATE: str = ""

REQUESTS_PER_CITIZEN: float = 1.0


def _severity_word(avg_severity: float) -> str:
    """Map an average 1-5 severity to plain adjectives for the copy."""
    if avg_severity >= 4.5:
        return "critical"
    if avg_severity >= 3.5:
        return "high-severity"
    if avg_severity >= 2.5:
        return "moderate"
    return "cosmetic"


def _population_word(density: float) -> str:
    """Describe population exposure from the density lookup."""
    if density >= 20000:
        return "a dense urban population"
    if density >= 10000:
        return "a significant population"
    return "a smaller local population"


def generate_recommendation(
    category: str,
    count: int,
    avg_severity: float,
    region_name: str,
    days_open: float,
    priority_score: float,
) -> str:
    """Build a plain-language recommendation string for a hotspot.

    Pure template expansion of pre-computed numbers -- fully explainable and
    deterministic, so judges can trace the wording straight back to the data.
    """
    severity = _severity_word(avg_severity)
    density = density_for_region(region_name)
    population = _population_word(density)

    if priority_score >= 75:
        action = "urgent budget allocation"
    elif priority_score >= 50:
        action = "immediate budget allocation"
    else:
        action = "budget allocation"

    return (
        f"{severity.capitalize()} {category} issue affecting {count} citizens "
        f"near {region_name} ({population}), unresolved for "
        f"{math.ceil(days_open)} days -- recommend {action} for "
        f"{category} repair/upgrade."
    )