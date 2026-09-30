"""Sync service: pull the mesh into local snapshots, then recompute.

Two separate operations, deliberately not fused:

``sync``
    Copies mesh rows into the snapshot tables. Running it twice with unchanged
    mesh data produces no duplicate windows, so it is safe to run on a schedule.

``analyse``
    Recomputes hotspots, trends and risks from the snapshots. It never writes to
    a snapshot table, so re-running the analytics cannot alter the history it is
    analysing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.utils import utcnow
from app.repositories.intelligence_repository import IntelligenceRepository
from app.services import hotspot_service, mesh_client, risk_service, trend_service
from app.services.hotspot_service import WardSignal

logger = get_logger(__name__)


@dataclass
class SyncResult:
    """Outcome of one sync pass."""

    synced_at: datetime
    counts: dict[str, int] = field(default_factory=dict)
    inserted: dict[str, int] = field(default_factory=dict)
    mesh_available: bool = True

    def as_dict(self) -> dict:
        return {
            "synced_at": self.synced_at.isoformat(),
            "counts": self.counts,
            "inserted": self.inserted,
            "mesh_available": self.mesh_available,
        }


@dataclass
class AnalysisResult:
    """Outcome of one analytics pass."""

    run_id: int
    window_days: int
    wards_analysed: int
    hotspots: int
    trends: int
    risks: int
    tier_counts: dict[str, int]
    direction_counts: dict[str, int]
    risk_summary: dict
    duration_ms: int

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "window_days": self.window_days,
            "wards_analysed": self.wards_analysed,
            "hotspots": self.hotspots,
            "trends": self.trends,
            "risks": self.risks,
            "tiers": self.tier_counts,
            "directions": self.direction_counts,
            "risks_summary": self.risk_summary,
            "duration_ms": self.duration_ms,
        }


def sync_from_mesh(
    db: Session,
    *,
    districts: Optional[Sequence[str]] = None,
    database_url: Optional[str] = None,
) -> SyncResult:
    """Copy the mesh's current state into local snapshots."""
    started = time.perf_counter()
    repo = IntelligenceRepository(db)

    if not mesh_client.mesh_available(database_url):
        logger.warning("mesh_unavailable", extra={"url": database_url or settings.mesh_database_url})
        return SyncResult(synced_at=utcnow(), mesh_available=False)

    snapshot = mesh_client.fetch_snapshot(database_url, districts=list(districts) if districts else None)
    now = utcnow()
    inserted = repo.store_snapshot(snapshot, snapshot_at=now)
    db.commit()

    counts = snapshot.counts()
    logger.info(
        "mesh_synced",
        extra={**counts, **inserted, "ms": int((time.perf_counter() - started) * 1000)},
    )
    return SyncResult(
        synced_at=now, counts=counts, inserted=inserted, mesh_available=True
    )


def build_ward_signals(
    repo: IntelligenceRepository, *, window_days: int = 30
) -> list[WardSignal]:
    """Turn demand windows into per-ward signals for clustering.

    Only the *latest* window of the requested length contributes. Consecutive
    30-day windows share a ``window_days`` value, so summing every matching row
    would count the whole history and make a hotspot look worse every time
    another window is synced.
    """
    signals: dict[str, WardSignal] = {}
    for location in repo.list_ward_locations():
        signals[location.ward_code] = WardSignal(
            ward_code=location.ward_code,
            name=location.name,
            district=location.district,
            latitude=location.latitude,
            longitude=location.longitude,
            population=location.population,
        )

    for row in repo.list_latest_demand(window_days=window_days):
        signal = signals.get(row.ward_code)
        if signal is None:
            continue
        signal.complaints += int(row.complaint_count or 0)
        signal.critical_complaints += int(row.critical_count or 0)
        signal.peak_severity = max(signal.peak_severity, float(row.avg_severity or 0.0))
        if row.sector not in signal.sectors:
            signal.sectors.append(row.sector)

    return list(signals.values())


def analyse(
    db: Session,
    *,
    window_days: Optional[int] = None,
    sectors: Optional[Sequence[str]] = None,
    persist: bool = True,
) -> AnalysisResult:
    """Recompute hotspots, trends and emerging risks from the snapshots."""
    started = time.perf_counter()
    repo = IntelligenceRepository(db)
    window = window_days or settings.trend_windows_days[0]

    run = repo.start_run(kind="analysis", window_days=window)
    db.flush()

    ward_signals = build_ward_signals(repo, window_days=window)
    hotspots = hotspot_service.detect_hotspots(ward_signals, window_days=window)
    tier_counts = hotspot_service.summarise_tiers(hotspots)

    demand_rows = repo.list_demand()
    trends = trend_service.compute_all_trends(demand_rows, sectors=sectors)
    direction_counts = trend_service.summarise_directions(trends)

    projects = repo.list_projects()
    risks = risk_service.detect_risks(
        demand_rows=demand_rows,
        gap_snapshots=repo.list_gaps(),
        projects=projects,
        project_elapsed=project_elapsed_fractions(projects),
    )
    risk_summary = risk_service.summarise_risks(risks)

    if persist:
        repo.replace_hotspots(hotspots, window_days=window)
        repo.replace_trends(trends)
        repo.replace_risks(risks, run_id=run.id)

    duration_ms = int((time.perf_counter() - started) * 1000)
    repo.finish_run(
        run,
        wards_analysed=len(ward_signals),
        hotspots_created=len(hotspots),
        trends_computed=len(trends),
        risks_detected=len(risks),
        sync_counts={},
        duration_ms=duration_ms,
    )
    db.commit()

    logger.info(
        "analysis_complete",
        extra={
            "wards": len(ward_signals),
            "hotspots": len(hotspots),
            "trends": len(trends),
            "risks": len(risks),
            "ms": duration_ms,
        },
    )
    return AnalysisResult(
        run_id=run.id,
        window_days=window,
        wards_analysed=len(ward_signals),
        hotspots=len(hotspots),
        trends=len(trends),
        risks=len(risks),
        tier_counts=tier_counts,
        direction_counts=direction_counts,
        risk_summary=risk_summary,
        duration_ms=duration_ms,
    )


def project_elapsed_fractions(projects: Sequence) -> dict[str, float]:
    """Share of each project's sanctioned window that has already passed."""
    fractions: dict[str, float] = {}
    today = utcnow().date()
    for project in projects:
        start = project.sanctioned_on
        end = project.expected_completion_on
        if start is None or end is None:
            continue
        start_date = start.date() if isinstance(start, datetime) else start
        end_date = end.date() if isinstance(end, datetime) else end
        if end_date <= start_date:
            fractions[project.project_code] = 1.0
            continue
        total_days = (end_date - start_date).days
        elapsed = (today - start_date).days
        fractions[project.project_code] = max(0.0, min(1.0, elapsed / total_days)) if total_days else 1.0
    return fractions


def sync_and_analyse(
    db: Session,
    *,
    districts: Optional[Sequence[str]] = None,
    window_days: Optional[int] = None,
    database_url: Optional[str] = None,
    persist: bool = True,
) -> dict:
    """Convenience wrapper: sync from the mesh, then analyse the result."""
    sync_result = sync_from_mesh(db, districts=districts, database_url=database_url)
    if not sync_result.mesh_available:
        return {"sync": sync_result.as_dict(), "analysis": None}
    analysis = analyse(db, window_days=window_days, persist=persist)
    return {"sync": sync_result.as_dict(), "analysis": analysis.as_dict()}