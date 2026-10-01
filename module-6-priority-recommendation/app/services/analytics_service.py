"""Orchestration: the recompute run and the read-side views.

This is the only layer that knows how a snapshot, a score, a recommendation and
a run record fit together. Services below it are pure; repositories below it are
persistence. Everything a caller wants - a ranking, a summary, a plan, health -
is assembled here.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import FACTOR_LABELS, HealthStatus, PriorityBand, RunStatus
from app.database.connection import SessionLocal, database_exists, init_db
from app.repositories.priority_repository import PriorityRepository
from app.services.intelligence_client import (
    IntelligenceClient,
    IntelligenceSnapshot,
    IntelligenceUnavailable,
)
from app.services.priority_service import score_snapshot
from app.services.recommendation_service import build_action_plan, build_cost_lakhs, build_recommendation

logger = logging.getLogger(__name__)


class NothingScoredError(RuntimeError):
    """The ranking has not been produced yet.

    Distinct from an upstream failure: the service is healthy, there is simply no
    ranking to show. The API turns this into a 409 so the dashboard can say
    "nothing scored yet" rather than "something broke".
    """


def recompute(
    session: Session, *, trigger: str = "manual", client: IntelligenceClient | None = None
) -> dict[str, Any]:
    """Score every hotspot in the current snapshot and install the result.

    The run record is created first and always closed out, including on failure,
    so a failed recompute is visible in ``/operations/runs`` instead of leaving
    the previous ranking looking current.
    """
    init_db()
    repo = PriorityRepository(session)
    client = client or IntelligenceClient()

    run = repo.start_run(trigger=trigger, source=client.database_url)
    started = time.perf_counter()
    try:
        snapshot = client.fetch_snapshot()
        scored = score_snapshot(snapshot.hotspots, settings)

        for row in scored:
            row["recommended_cost_lakhs"] = build_cost_lakhs(row, settings)
            row["recommendation"] = build_recommendation(row, settings)

        repo.replace_scores(scored, run_id=run.id, source=snapshot.source)
        duration_ms = int((time.perf_counter() - started) * 1000)
        repo.finish_run(run, RunStatus.SUCCESS.value, hotspots_scored=len(scored))

        logger.info(
            "scored %d hotspots from %s in %dms (run %d)",
            len(scored),
            snapshot.source,
            duration_ms,
            run.id,
        )
        return {
            "run_id": run.id,
            "scored": len(scored),
            "source": snapshot.source,
            "degraded_factors": list(snapshot.degraded_factors),
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        session.rollback()
        logger.exception("recompute failed")
        try:
            run = repo.start_run(trigger=trigger, source=client.database_url)
            repo.finish_run(run, RunStatus.FAILED.value, error=str(exc))
        except Exception:  # pragma: no cover - recording the failure must not mask it
            logger.exception("could not record failed run")
        raise


def _movement(repo: PriorityRepository, hotspot_code: str) -> dict[str, Any]:
    return repo.movement_for(hotspot_code)


def priorities_payload(session: Session) -> dict[str, Any]:
    """The full ranking, each row carrying its evidence and movement."""
    repo = PriorityRepository(session)
    rows = repo.scored_rows()
    if not rows:
        raise NothingScoredError(
            "No priority scores yet. POST /api/v1/operations/recompute to produce them."
        )

    latest = repo.latest_successful_run()
    items = []
    for row in rows:
        factors = row.factors or {}
        items.append(
            {
                "hotspot_code": row.hotspot_code,
                "district": row.district,
                "rank": row.rank,
                "score": row.score,
                "band": row.band,
                "centroid_latitude": row.centroid_latitude,
                "centroid_longitude": row.centroid_longitude,
                "dominant_sector": row.dominant_sector,
                "sectors": row.sectors or [],
                "ward_codes": row.ward_codes or [],
                "population": row.population,
                "total_complaints": row.total_complaints,
                "critical_complaints": row.critical_complaints,
                "unmeasured_factors": row.unmeasured_factors or [],
                "factors": factors,
                "factor_labels": {
                    name: FACTOR_LABELS.get(name, name) for name in factors
                },
                "evidence": row.evidence or {},
                "recommendation": row.recommendation,
                "recommended_cost_lakhs": row.recommended_cost_lakhs,
                "movement": _movement(repo, row.hotspot_code),
                "computed_at": row.computed_at.isoformat() if row.computed_at else None,
                "intelligence_source": row.intelligence_source,
            }
        )

    return {
        "priorities": items,
        "count": len(items),
        "bands": band_counts(items),
        "source": (latest.intelligence_source if latest else None),
        "run_id": (latest.id if latest else None),
        "generated_at": (latest.finished_at.isoformat() if latest and latest.finished_at else None),
    }


def band_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Count of hotspots in each band, always reporting all three bands.

    All three keys are always present so the dashboard can render a full band
    legend without treating a missing key as zero.
    """
    counts = {band.value: 0 for band in PriorityBand}
    for item in items:
        counts[item["band"]] = counts.get(item["band"], 0) + 1
    return counts


def summary_payload(session: Session) -> dict[str, Any]:
    """Portfolio-level totals for the dashboard header."""
    repo = PriorityRepository(session)
    rows = repo.scored_rows()
    if not rows:
        raise NothingScoredError(
            "No priority scores yet. POST /api/v1/operations/recompute to produce them."
        )
    latest = repo.latest_successful_run()
    return {
        "scored": len(rows),
        "districts": len({row.district for row in rows}),
        "bands": {band.value: 0 for band in PriorityBand}
        | {
            band.value: sum(1 for row in rows if row.band == band.value)
            for band in PriorityBand
        },
        "population_covered": sum(row.population or 0 for row in rows),
        "complaints_covered": sum(row.total_complaints or 0 for row in rows),
        "critical_complaints": sum(row.critical_complaints or 0 for row in rows),
        "total_cost_lakhs": round(
            sum(row.recommended_cost_lakhs or 0.0 for row in rows), 1
        ),
        "average_score": round(sum(row.score or 0.0 for row in rows) / len(rows), 1),
        "hotspots_with_unmeasured_factors": sum(
            1 for row in rows if row.unmeasured_factors
        ),
        "run_id": latest.id if latest else None,
        "source": latest.intelligence_source if latest else None,
        "generated_at": latest.finished_at.isoformat() if latest and latest.finished_at else None,
    }


def plan_payload(session: Session, size: int = 10) -> dict[str, Any]:
    """Funding plan in rank order, with a running cumulative envelope."""
    repo = PriorityRepository(session)
    rows = repo.scored_rows()
    if not rows:
        raise NothingScoredError(
            "No priority scores yet. POST /api/v1/operations/recompute to produce them."
        )

    from app.models.priority_tables import PriorityScore

    as_dicts = [
        {
            "hotspot_code": r.hotspot_code,
            "district": r.district,
            "rank": r.rank,
            "score": r.score,
            "band": r.band,
            "dominant_sector": r.dominant_sector,
            "ward_codes": r.ward_codes or [],
            "recommendation": r.recommendation,
            "recommended_cost_lakhs": r.recommended_cost_lakhs,
            "population": r.population,
            "total_complaints": r.total_complaints,
        }
        for r in rows
    ]
    plan = build_action_plan(as_dicts, settings)[: max(1, size)]
    return {
        "items": plan,
        "count": len(plan),
        "total_cost_lakhs": round(sum(i["recommended_cost_lakhs"] for i in plan), 1),
        "cumulative_cost_lakhs": plan[-1]["cumulative_cost_lakhs"] if plan else 0.0,
        "is_partial": len(rows) > len(plan),
    }


def history_payload(session: Session, hotspot_code: str) -> dict[str, Any]:
    repo = PriorityRepository(session)
    current = repo.get_score(hotspot_code)
    if current is None:
        raise KeyError(hotspot_code)
    entries = [
        {
            "run_id": entry.run_id,
            "rank": entry.rank,
            "score": entry.score,
            "band": entry.band,
            "recorded_at": entry.recorded_at.isoformat() if entry.recorded_at else None,
        }
        for entry in reversed(repo.history_for(hotspot_code))
    ]
    return {
        "hotspot_code": hotspot_code,
        "current": {
            "rank": current.rank,
            "score": current.score,
            "band": current.band,
        },
        "entries": entries,
        "count": len(entries),
    }


def health_payload(session: Session) -> dict[str, Any]:
    """Report service health and whether a ranking exists.

    Upstream availability and scoring status are answered separately because
    they mean different things to a dashboard: Module 5 being unreachable is an
    operational problem, and having nothing scored is a workflow one.
    """
    repo = PriorityRepository(session)
    scored = repo.count_scores()
    available = database_exists(settings.intelligence_database_url)

    degraded: list[str] = []
    if not available:
        status = HealthStatus.UNAVAILABLE.value
        degraded.append("Module 5 intelligence database not found")
    else:
        try:
            snapshot = IntelligenceClient().fetch_snapshot()
            degraded.extend(snapshot.degraded_factors)
            status = HealthStatus.DEGRADED.value if snapshot.degraded_factors else HealthStatus.HEALTHY.value
        except IntelligenceUnavailable as exc:
            status = HealthStatus.UNAVAILABLE.value
            degraded.append(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            status = HealthStatus.UNAVAILABLE.value
            degraded.append(f"unexpected error reading intelligence: {exc}")

    latest = repo.latest_successful_run()
    return {
        "status": status,
        "service": "module-6-priority-recommendation",
        "scored": scored,
        "intelligence_available": available,
        "intelligence_source": settings.intelligence_database_url,
        "degraded_factors": degraded,
        "degraded_factor_labels": [
            FACTOR_LABELS.get(f, f) for f in degraded if f in FACTOR_LABELS
        ],
        "runs": repo.run_count(),
        "latest_run": {
            "id": latest.id,
            "status": latest.status,
            "hotspots_scored": latest.hotspots_scored,
            "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
        }
        if latest
        else None,
        "database_url": settings.database_url,
    }


def intelligence_health_payload() -> dict[str, Any]:
    """Details of the Module 5 snapshot, for diagnosing degraded rankings."""
    available = database_exists(settings.intelligence_database_url)
    if not available:
        return {
            "available": False,
            "source": settings.intelligence_database_url,
            "detail": "database not found - run module-5 seed_intelligence.py",
            "table_presence": {},
        }
    client = IntelligenceClient()
    presence = client.table_presence()
    try:
        snapshot = client.fetch_snapshot()
        return {
            "available": True,
            "source": snapshot.source,
            "hotspots": len(snapshot.hotspots),
            "degraded_factors": list(snapshot.degraded_factors),
            "table_presence": presence,
            "missing_tables": [name for name, ok in presence.items() if not ok],
        }
    except IntelligenceUnavailable as exc:
        return {
            "available": False,
            "source": snapshot_source(client),
            "detail": str(exc),
            "table_presence": presence,
        }


def snapshot_source(client: IntelligenceClient) -> str:
    return client.database_url


def runs_payload(session: Session, limit: int = 20) -> dict[str, Any]:
    repo = PriorityRepository(session)
    return {
        "runs": [
            {
                "id": run.id,
                "status": run.status,
                "trigger": run.trigger,
                "hotspots_scored": run.hotspots_scored,
                "source": run.intelligence_source,
                "duration_ms": run.duration_ms,
                "error": run.error,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            }
            for run in repo.recent_runs(limit)
        ],
        "count": len(repo.recent_runs(limit)),
    }


__all__ = [
    "recompute",
    "priorities_payload",
    "summary_payload",
    "plan_payload",
    "history_payload",
    "health_payload",
    "intelligence_health_payload",
    "runs_payload",
    "NothingScoredError",
]