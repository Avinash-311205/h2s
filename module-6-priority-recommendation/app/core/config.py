"""Scoring policy for Module 6.

Everything a policymaker could reasonably argue about lives here: the relative
weight of each factor, the reference values the raw numbers are compared
against, the band cut-offs, and the cost model. Keeping it in one module means a
disagreement about the output is a disagreement about *this file*, not a hunt
through the scoring code.

The references are deliberately fixed constants rather than values derived from
the current dataset. A percentile taken from today's data would make the same
raw complaint count score differently next month purely because the data moved,
which would quietly destroy the comparability that the history table exists to
provide. Policy thresholds should only move when a human changes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ScoringWeights:
    """Relative weight of each factor in the composite score.

    Demand and trend carry the most weight because they answer "how much, and
    is it getting worse", which is what a budget decision turns on. Coverage is
    weighted low on purpose: it is a data-quality modifier, not a measure of
    need, and letting it drive the score would reward districts that happen to
    report well.
    """

    demand: float = 0.30
    severity: float = 0.20
    trend: float = 0.20
    coverage: float = 0.15
    service_failure: float = 0.15

    def as_dict(self) -> dict[str, float]:
        return {
            "demand": self.demand,
            "severity": self.severity,
            "trend": self.trend,
            "coverage": self.coverage,
            "service_failure": self.service_failure,
        }

    def validate(self) -> None:
        total = sum(self.as_dict().values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"scoring weights must sum to 1.0, got {total:.6f}")


@dataclass(frozen=True)
class ReferenceValues:
    """Fixed reference points that turn raw measurements into comparable ratios.

    Each ``ref_*`` value is the raw measurement that scores a *full* 1.0. A
    measurement at half the reference scores 0.5. Anything above the reference
    saturates, because "twice as bad as the reference" should not push the
    composite past what the other factors can justify.
    """

    # Demand is complaints per 1,000 residents per window. Normalising by
    # population is what stops a large but quiet ward from outranking a small
    # but genuinely distressed one.
    #
    # Calibrated against the seeded Tamil Nadu mesh, where 30-day rates run
    # 0.8-2.5 per 1,000. A reference of 25 would put every hotspot at under 10%
    # of the factor's range, silently reducing the heaviest-weighted factor to
    # an effective weight of about 0.03. Five per 1,000 residents filing a
    # complaint in a month is genuinely elevated, and it spreads the observed
    # data across roughly 0.16-0.50 of the range rather than pinning it near
    # zero. This is a policy judgement, not a measurement - see the README note
    # on recalibrating references.
    ref_max_complaints_per_1000: float = 5.0

    # Severity is already on a 1-5 scale, so the reference is the top of it.
    ref_max_avg_severity: float = 5.0

    # Trend is a growth ratio: 100% growth in complaints scores 1.0.
    ref_max_pct_growth: float = 100.0

    # Coverage is a 0-100 completeness percentage, so the reference is 100%.
    ref_max_gap_pct: float = 100.0

    # Service failure is the share of budget stuck in stalled projects, 0-1.
    ref_max_stalled_share: float = 0.40

    # Delivery slippage in days.
    ref_max_delay_days: float = 180.0

    # Unspent budget share.
    ref_max_unspent_share: float = 0.50


@dataclass(frozen=True)
class BandThresholds:
    """Priority bands, cut on the 0-100 composite score.

    These are policy statements, not statistics. The gap between HIGH and MEDIUM
    is intentionally wide because the operational response differs sharply above
    and below it.
    """

    high: float = 70.0
    medium: float = 45.0


@dataclass(frozen=True)
class CostModel:
    """Indicative budget envelope per hotspot.

    These are planning envelopes expressed in lakhs, not cost estimates. A
    hotspot with no evidence of money being stuck gets the base envelope; one
    with stalled or unspent money gets more, because the problem there is
    delivery rather than allocation.
    """

    base_lakhs: float = 50.0
    per_ward_lakhs: float = 5.0
    stalled_extra_lakhs: float = 15.0
    unspent_extra_lakhs: float = 10.0
    critical_extra_lakhs: float = 10.0


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    Database URLs are read from the environment so the module can be pointed at a
    different intelligence snapshot without a code change, but every default
    points at the sibling modules' seeded databases.
    """

    database_url: str = "sqlite:///./priority_recommendation.db"
    intelligence_database_url: str = (
        f"sqlite:///{MODULE_DIR.parent / 'module-5-civic-intelligence' / 'civic_intelligence.db'}"
    )

    api_host: str = "0.0.0.0"
    api_port: int = 8006

    window_days: int = 30

    weights: ScoringWeights = field(default_factory=ScoringWeights)
    references: ReferenceValues = field(default_factory=ReferenceValues)
    bands: BandThresholds = field(default_factory=BandThresholds)
    costs: CostModel = field(default_factory=CostModel)

    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        import os

        default = cls()
        default.weights.validate()
        return cls(
            database_url=os.getenv("DATABASE_URL", default.database_url),
            intelligence_database_url=os.getenv(
                "INTELLIGENCE_DATABASE_URL", default.intelligence_database_url
            ),
            api_host=os.getenv("API_HOST", default.api_host),
            api_port=int(os.getenv("API_PORT", default.api_port)),
            log_level=os.getenv("LOG_LEVEL", default.log_level),
        )


settings = Settings.from_env()