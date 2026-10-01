"""HTTP routes for Module 6.

Status codes carry meaning here rather than being incidental. ``409`` means the
service is fine but has not produced a ranking yet; ``503`` means the upstream
intelligence database is not there. The dashboard distinguishes them, because
"nothing scored yet" is a workflow state the user can act on and "Module 5 is
missing" is not.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.connection import get_session
from app.services import analytics_service
from app.services.analytics_service import NothingScoredError
from app.services.intelligence_client import IntelligenceUnavailable

logger = logging.getLogger(__name__)

router = APIRouter()


def _read(session: Session, fn, *args, **kwargs):
    """Run a read payload function, translating domain errors to status codes."""
    try:
        return fn(session, *args, **kwargs)
    except NothingScoredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown hotspot: {exc}") from exc


@router.get("/health")
def health(session: Session = Depends(get_session)):
    return analytics_service.health_payload(session)


@router.get("/health/intelligence")
def health_intelligence():
    return analytics_service.intelligence_health_payload()


@router.get("/api/v1/priorities")
def priorities(
    district: str | None = Query(default=None),
    band: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    """The current ranking, newest score per hotspot.

    Filters are applied after loading rather than in SQL because the ranking is a
    single small table rebuilt each run, and a round trip to re-query on every
    filter combination would complicate the code for no measurable gain.
    """
    payload = _read(session, analytics_service.priorities_payload)
    items = payload["priorities"]
    if district:
        items = [i for i in items if i["district"].lower() == district.lower()]
    if band:
        wanted = band.upper()
        if wanted not in {"HIGH", "MEDIUM", "LOW"}:
            raise HTTPException(status_code=422, detail=f"unknown band: {band}")
        items = [i for i in items if i["band"] == wanted]
    return {**payload, "priorities": items, "count": len(items)}


@router.get("/api/v1/priorities/summary")
def summary(session: Session = Depends(get_session)):
    return _read(session, analytics_service.summary_payload)


@router.get("/api/v1/plan")
def plan(
    size: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_session),
):
    return _read(session, analytics_service.plan_payload, size)


@router.get("/api/v1/priorities/{hotspot_code}/history")
def history(hotspot_code: str, session: Session = Depends(get_session)):
    return _read(session, analytics_service.history_payload, hotspot_code)


@router.get("/api/v1/operations/runs")
def runs(
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_session),
):
    return analytics_service.runs_payload(session, limit)


@router.post("/api/v1/operations/recompute")
def recompute(session: Session = Depends(get_session)):
    """Rescore from the current Module 5 snapshot.

    Takes no body: the snapshot location is configuration, not a per-request
    choice, so that a recompute always runs against the intelligence database the
    service was started with.
    """
    try:
        return analytics_service.recompute(session, trigger="api")
    except IntelligenceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - already recorded on the run
        logger.exception("recompute failed")
        raise HTTPException(status_code=500, detail=f"recompute failed: {exc}") from exc