"""Reads Module 5's intelligence database and turns it into scoring signals.

This module owns the awkward part of the contract between the two modules:
deciding what counts as *missing*. A missing input is not a zero. If Module 5 has
no trend for a ward-sector, the honest result is "this factor was not measured"
- not "trend is fine". Silently treating absence as zero is how a ranking ends up
claiming a district is low priority because nobody reported anything, which is
the exact failure a priority engine exists to prevent.

So every signal carries a ``measured`` flag, and the set of unmeasured signals is
surfaced all the way through to the API and the dashboard.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.enums import ScoreFactor, TrendDirection
from app.database.connection import build_engine, database_exists

logger = logging.getLogger(__name__)

#: Tables Module 6 reads. If any is absent the snapshot is from an older Module 5
#: and the affected factors are degraded rather than failing the whole run.
REQUIRED_TABLES = (
    "hotspots",
    "demand_windows",
    "gap_snapshots",
    "project_snapshots",
    "trends",
)


class IntelligenceUnavailable(RuntimeError):
    """Module 5's database cannot be read at all."""


@dataclass(frozen=True)
class Signal:
    """One factor's raw value, its 0-1 component and whether it was measurable.

    ``value`` is ``None`` when unmeasured, which is what makes
    ``measured=False`` unambiguous - a measured zero and a missing measurement
    are different facts about the world.
    """

    factor: str
    value: Optional[float]
    component: float
    weight: float
    measured: bool
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "value": self.value,
            "component": round(self.component, 4),
            "weight": self.weight,
            "measured": self.measured,
            "note": self.note,
        }


@dataclass
class HotspotSignals:
    """Everything Module 6 knows about one hotspot, after aggregation."""

    hotspot_code: str
    district: str
    centroid_latitude: float
    centroid_longitude: float
    ward_codes: list[str]
    sectors: list[str]
    population: int
    total_complaints: int
    critical_complaints: int
    window_days: int

    demand_rate_per_1000: Optional[float] = None
    avg_severity: Optional[float] = None
    pct_growth: Optional[float] = None
    trend_direction: str = TrendDirection.UNKNOWN
    gap_score: Optional[float] = None
    stalled_share: Optional[float] = None
    unspent_share: Optional[float] = None
    delay_days: Optional[float] = None
    dominant_sector: Optional[str] = None
    unmeasured_factors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntelligenceSnapshot:
    """A consistent read of Module 5's database.

    ``degraded_factors`` names whole *factor groups* that could not be computed,
    as distinct from :attr:`HotspotSignals.unmeasured_factors` which is per
    hotspot. A degraded factor means the table was missing or unreadable; an
    unmeasured factor means the table was fine but had no row for this hotspot.
    """

    source: str
    hotspots: list[HotspotSignals]
    available: bool
    degraded_factors: tuple[str, ...] = ()
    table_presence: dict[str, bool] = field(default_factory=dict)

    @property
    def degraded_factor_names(self) -> list[str]:
        """Degraded factor groups, named for the API."""
        from app.core.enums import FACTOR_LABELS

        return [FACTOR_LABELS.get(f, f) for f in self.degraded_factors]


def _mean(values: Iterable[float]) -> Optional[float]:
    """Mean of a non-empty iterable, or ``None`` if there is nothing to average."""
    items = [v for v in values if v is not None]
    if not items:
        return None
    return sum(items) / len(items)


def _weighted_growth(direction: str, pct_growth: Optional[float]) -> float:
    """Convert a Module 5 trend direction into a 0-1 severity, where 1.0 means alarming.

    A raw percentage change is not symmetric: worsening by 590% and improving by
    590% are very different situations with the same magnitude. Worsening trends
    map into the positive half where the reference point matters, improving
    trends map to 0 (a real gain, not a risk), and stable stays neutral so it
    neither rewards nor punishes a ward that is simply steady.

    Directions are matched against :class:`TrendDirection`, so an unrecognised
    value from a newer Module 5 degrades to neutral instead of being treated as
    healthy.
    """
    try:
        resolved = TrendDirection(direction)
    except ValueError:
        logger.warning("unrecognised trend direction %r from upstream", direction)
        return 0.0

    if resolved is TrendDirection.STABLE:
        return 0.25
    if resolved is TrendDirection.IMPROVING:
        return 0.0
    if resolved is TrendDirection.WORSENING:
        if pct_growth is None:
            return 0.5  # worsening but unquantified: assume middling, flag it
        ref = settings.references.ref_max_pct_growth
        return max(0.0, min(1.0, pct_growth / ref)) if ref else 0.0
    return 0.0


class IntelligenceClient:
    """Reads Module 5's database directly.

    Direct SQLite access rather than an HTTP call to Module 5's API: Module 5 is
    a batch job, not a service, and requiring it to be running in order to score
    a snapshot would add a dependency for no benefit.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or settings.intelligence_database_url
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        """Lazily opened read-only engine."""
        if self._engine is None:
            if not database_exists(self.database_url):
                raise IntelligenceUnavailable(
                    f"Module 5 database not found at {self.database_url}. "
                    "Run module-5-civic-intelligence/seed_intelligence.py first."
                )
            self._engine = build_engine(self.database_url, read_only=True)
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        """A pooled connection that is always returned.

        Every query in a snapshot read shares one connection. Opening a
        connection per query would work until the pool filled - each of the five
        signals is queried once per hotspot, so a realistic snapshot exhausts a
        default pool and the run fails on a connection timeout rather than on
        anything to do with the data.
        """
        conn = self.engine.connect()
        try:
            yield conn
        finally:
            conn.close()

    # ---- snapshot ---------------------------------------------------------

    def table_presence(self, conn: Connection | None = None) -> dict[str, bool]:
        """Which of the tables Module 6 reads actually exist."""
        try:
            if conn is not None:
                rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
                present = {row[0] for row in rows}
            else:
                with self._connection() as own:
                    rows = own.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                    present = {row[0] for row in rows}
        except SQLAlchemyError as exc:
            raise IntelligenceUnavailable(f"cannot inspect intelligence db: {exc}") from exc
        return {table: table in present for table in REQUIRED_TABLES}

    def fetch_snapshot(self) -> IntelligenceSnapshot:
        """Read one consistent snapshot of Module 5's outputs.

        Never raises for a *partial* upstream: missing tables degrade the
        affected factors and the ranking continues, because a ranking built from
        three of five factors is far more useful to a policymaker than an error
        page. It does raise when the database is entirely unreadable, since that
        leaves nothing to rank on.
        """
        with self._connection() as conn:
            presence = self.table_presence(conn)
            return self._fetch_on(conn, presence)

    def _fetch_on(self, conn: Connection, presence: dict[str, bool]) -> IntelligenceSnapshot:
        """Read the snapshot using an already-open connection."""
        degraded: list[str] = []
        if not presence.get("hotspots", False):
            raise IntelligenceUnavailable(
                "Module 5 database has no 'hotspots' table. Re-run seed_intelligence.py."
            )
        if not presence.get("demand_windows", False):
            degraded.extend([ScoreFactor.DEMAND, ScoreFactor.SEVERITY])
        if not presence.get("gap_snapshots", False):
            degraded.append(ScoreFactor.COVERAGE)
        if not presence.get("project_snapshots", False):
            degraded.append(ScoreFactor.SERVICE_FAILURE)
        if not presence.get("trends", False):
            degraded.append(ScoreFactor.TREND)

        hotspots = self._read_hotspots(conn)
        if not hotspots:
            logger.warning("Module 5 snapshot contains no hotspots")

        signals = [
            self._enrich(conn, hotspot, presence, degraded) for hotspot in hotspots
        ]
        return IntelligenceSnapshot(
            source=self.database_url,
            hotspots=signals,
            available=True,
            degraded_factors=tuple(dict.fromkeys(degraded)),
            table_presence=presence,
        )

    # ---- per-hotspot assembly --------------------------------------------

    def _read_hotspots(self, conn: Connection) -> list[dict[str, Any]]:
        rows = conn.execute(
            text(
                """
                SELECT hotspot_code, district, centroid_latitude, centroid_longitude,
                       ward_codes, sectors, population, total_complaints,
                       critical_complaints, window_days
                FROM hotspots
                ORDER BY hotspot_code
                """
            )
        )
        import json

        return [
            {
                "hotspot_code": row.hotspot_code,
                "district": row.district,
                "centroid_latitude": float(row.centroid_latitude or 0.0),
                "centroid_longitude": float(row.centroid_longitude or 0.0),
                "ward_codes": json.loads(row.ward_codes) if row.ward_codes else [],
                "sectors": json.loads(row.sectors) if row.sectors else [],
                "population": int(row.population or 0),
                "total_complaints": int(row.total_complaints or 0),
                "critical_complaints": int(row.critical_complaints or 0),
                "window_days": int(row.window_days or 30),
            }
            for row in rows
        ]

    def _enrich(
        self,
        conn: Connection,
        hotspot: dict[str, Any],
        presence: dict[str, bool],
        degraded: list[str],
    ) -> HotspotSignals:
        """Attach demand, trend, gap and delivery figures to one hotspot."""
        wards: list[str] = hotspot["ward_codes"] or []
        sectors: list[str] = hotspot["sectors"] or []
        signals = HotspotSignals(
            hotspot_code=hotspot["hotspot_code"],
            district=hotspot["district"],
            centroid_latitude=hotspot["centroid_latitude"],
            centroid_longitude=hotspot["centroid_longitude"],
            ward_codes=wards,
            sectors=sectors,
            population=hotspot["population"],
            total_complaints=hotspot["total_complaints"],
            critical_complaints=hotspot["critical_complaints"],
            window_days=hotspot["window_days"],
        )

        ward_in = ",".join(f":w{i}" for i in range(len(wards))) or "NULL"
        ward_params = {f"w{i}": w for i, w in enumerate(wards)}

        if presence.get("demand_windows", False) and wards:
            demand = conn.execute(
                text(
                    f"""
                    SELECT SUM(complaint_count) AS complaints,
                           SUM(critical_count) AS critical,
                           AVG(avg_severity) AS severity,
                           SUM(population) AS population,
                           SUM(CASE WHEN sector = 'ELECTRICITY' THEN complaint_count
                                    ELSE 0 END) AS elec
                    FROM demand_windows
                    WHERE ward_code IN ({ward_in})
                    """
                ),
                ward_params,
            ).one()
            population = int(demand.population or 0) or signals.population
            complaints = int(demand.complaints or 0)
            # Rate, not volume: a large ward with the same complaint count as a
            # small one is not in the same situation.
            signals.demand_rate_per_1000 = (
                round(complaints / population * 1000.0, 3) if population > 0 else None
            )
            signals.avg_severity = (
                round(float(demand.severity), 3) if demand.severity is not None else None
            )
            signals.population = population
            if not population:
                signals.unmeasured_factors.append(ScoreFactor.DEMAND)

        if presence.get("trends", False) and wards:
            # Only trends whose sector the hotspot actually spans are considered:
            # a rising electricity trend in a hotspot with no electricity
            # complaints is a different ward's problem.
            sector_clause = "1=1"
            trend_params = dict(ward_params)
            if sectors:
                sector_clause = ",".join(f":s{i}" for i in range(len(sectors)))
                trend_params.update({f"s{i}": s for i, s in enumerate(sectors)})
            trend_rows = conn.execute(
                text(
                    f"""
                    SELECT direction, pct_change, momentum, ward_code, sector
                    FROM trends
                    WHERE ward_code IN ({ward_in}) AND sector IN ({sector_clause})
                    ORDER BY pct_change DESC
                    """
                ),
                trend_params,
            ).fetchall()
            if trend_rows:
                signals.trend_direction = str(trend_rows[0].direction or "UNKNOWN")
                signals.pct_growth = round(float(trend_rows[0].pct_change or 0.0), 2)
                # Dominant sector follows the trend data rather than the
                # alphabetical order of the sectors list.
                signals.dominant_sector = trend_rows[0].sector
            else:
                signals.unmeasured_factors.append(ScoreFactor.TREND)

        if presence.get("gap_snapshots", False) and wards:
            # Latest snapshot per ward-sector: averaging every snapshot ever taken
            # would let a gap that was fixed last year keep scoring.
            gap_rows = conn.execute(
                text(
                    f"""
                    SELECT ward_code, sector, gap_score, snapshot_at
                    FROM gap_snapshots AS g
                    WHERE ward_code IN ({ward_in})
                      AND snapshot_at = (
                          SELECT MAX(snapshot_at) FROM gap_snapshots AS s
                          WHERE s.ward_code = g.ward_code AND s.sector = g.sector
                      )
                    """
                ),
                ward_params,
            ).fetchall()
            if gap_rows:
                signals.gap_score = round(
                    _mean(float(r.gap_score or 0.0) for r in gap_rows) or 0.0, 2
                )
                if not signals.dominant_sector:
                    worst = max(gap_rows, key=lambda r: float(r.gap_score or 0.0))
                    signals.dominant_sector = worst.sector
            else:
                signals.unmeasured_factors.append(ScoreFactor.COVERAGE)

        if presence.get("project_snapshots", False) and wards:
            project_rows = conn.execute(
                text(
                    f"""
                    SELECT status, budget_lakhs, spent_lakhs, delay_days, sector
                    FROM project_snapshots
                    WHERE ward_code IN ({ward_in})
                    """
                ),
                ward_params,
            ).fetchall()
            if project_rows:
                signals.stalled_share = self._stalled_share(project_rows)
                signals.unspent_share = self._unspent_share(project_rows)
                signals.delay_days = round(
                    _mean(float(r.delay_days or 0.0) for r in project_rows) or 0.0, 1
                )
            else:
                signals.unmeasured_factors.append(ScoreFactor.SERVICE_FAILURE)

        for factor in degraded:
            if factor not in signals.unmeasured_factors:
                signals.unmeasured_factors.append(factor)
        signals.unmeasured_factors = list(dict.fromkeys(signals.unmeasured_factors))
        return signals

    @staticmethod
    def _stalled_share(rows: list[Any]) -> Optional[float]:
        """Share of budget sitting in projects that are not delivering.

        "Stalled" is defined by status rather than by age, because a young
        project is not stalled however old its deadline.
        """
        stalled_statuses = {"STALLED", "DELAYED", "FAILED", "SUSPENDED"}
        total = sum(float(r.budget_lakhs or 0.0) for r in rows)
        if total <= 0:
            return 0.0
        stalled = sum(
            float(r.budget_lakhs or 0.0) for r in rows if str(r.status) in stalled_statuses
        )
        return round(stalled / total, 4)

    @staticmethod
    def _unspent_share(rows: list[Any]) -> Optional[float]:
        """Share of sanctioned budget not yet spent.

        Only for projects that are actually underway - an unstarted project has
        spent nothing but is not a delivery failure.
        """
        active_statuses = {"IN_PROGRESS", "ONGOING", "DELAYED", "STALLED"}
        active = [r for r in rows if str(r.status) in active_statuses]
        if not active:
            return 0.0
        total = sum(float(r.budget_lakhs or 0.0) for r in active)
        if total <= 0:
            return 0.0
        spent = sum(float(r.spent_lakhs or 0.0) for r in active)
        return round(max(0.0, min(1.0, 1.0 - spent / total)), 4)

    def signals_for(
        self, snapshot: IntelligenceSnapshot, hotspot_code: str
    ) -> Optional[HotspotSignals]:
        """Look up one hotspot's signals within an already-read snapshot."""
        for hotspot in snapshot.hotspots:
            if hotspot.hotspot_code == hotspot_code:
                return hotspot
        return None


__all__ = [
    "IntelligenceClient",
    "IntelligenceSnapshot",
    "IntelligenceUnavailable",
    "HotspotSignals",
    "Signal",
]