"""HTTP surface for Module 5.

Four routers grouped by what they answer:

``/health``          is the module alive, and is the mesh reachable?
``/hotspots``        where is demand concentrated right now?
``/trends``          what is getting better or worse, and how fast?
``/risks``           what deserves attention before anyone asks?
``/operations``      sync from the mesh and run the analytics.

Reading and writing are kept apart: the intelligence endpoints are pure reads
over what the last run produced, so an analyst exploring the API can never
change the history another analyst is reasoning about.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    AnalysisOut,
    HealthOut,
    HotspotListOut,
    HotspotOut,
    HotspotSummaryOut,
    RiskListOut,
    RiskOut,
    RiskStatusIn,
    RiskSummaryOut,
    RunOut,
    SyncOut,
    TrendListOut,
    TrendOut,
    TrendSummaryOut,
    WardBriefOut,
)
from app.core.config import settings
from app.core.enums import HotspotTier, RiskStatus, TrendDirection
from app.core.logging import get_logger
from app.database.connection import get_db
from app.repositories.intelligence_repository import IntelligenceRepository
from app.services import analytics_service, hotspot_service, mesh_client, risk_service, trend_service

logger = get_logger(__name__)

health_router = APIRouter(tags=["health"])
hotspot_router = APIRouter(prefix="/hotspots", tags=["hotspots"])
trend_router = APIRouter(prefix="/trends", tags=["trends"])
risk_router = APIRouter(prefix="/risks", tags=["risks"])
ops_router = APIRouter(prefix="/operations", tags=["operations"])


# --- health ------------------------------------------------------------------
@health_router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    """Liveness plus whether the module has anything to serve."""
    repo = IntelligenceRepository(db)
    counts = repo.counts()
    districts = sorted({ward.district for ward in repo.list_ward_locations()})
    mesh_ready = mesh_client.mesh_available()
    return HealthOut(
        status="ok",
        service=settings.service_name,
        version=settings.version,
        database_ready=bool(counts["ward_locations"]),
        mesh_available=mesh_ready,
        counts=counts,
        districts=districts,
    )


@health_router.get("/health/mesh", response_model=dict)
def mesh_status() -> dict:
    """Details of the upstream mesh this module syncs from."""
    summary = mesh_client.mesh_url_summary()
    summary["sync_instructions"] = f"POST {settings.api_prefix}/operations/sync"
    return summary


# --- hotspots ----------------------------------------------------------------
def _hotspot_out(row) -> HotspotOut:
    return HotspotOut(
        hotspot_code=row.hotspot_code,
        district=row.district,
        latitude=row.centroid_latitude,
        longitude=row.centroid_longitude,
        ward_codes=list(row.ward_codes or []),
        ward_count=len(row.ward_codes or []),
        sectors=list(row.sectors or []),
        window_days=row.window_days,
        total_complaints=row.total_complaints,
        critical_complaints=row.critical_complaints,
        population=row.population,
        mean_intensity=row.mean_intensity,
        peak_intensity=row.peak_intensity,
        peak_severity=row.peak_severity,
        intensity_z_score=row.intensity_z_score,
        tier=row.tier,
        computed_at=row.computed_at,
    )


@hotspot_router.get("", response_model=HotspotListOut)
def list_hotspots(
    tier: Optional[str] = Query(None, description="CRITICAL | HIGH | MODERATE | NORMAL"),
    district: Optional[str] = None,
    window_days: Optional[int] = Query(None, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> HotspotListOut:
    """Hotspot clusters, worst intensity first."""
    if tier and tier not in {value.value for value in HotspotTier}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown tier: {tier}")
    repo = IntelligenceRepository(db)
    rows = repo.list_hotspots(tier=tier, district=district, window_days=window_days, limit=limit)
    return HotspotListOut(
        window_days=window_days or settings.trend_windows_days[0],
        count=len(rows),
        hotspots=[_hotspot_out(row) for row in rows],
    )


@hotspot_router.get("/summary", response_model=HotspotSummaryOut)
def hotspot_summary(
    window_days: Optional[int] = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
) -> HotspotSummaryOut:
    """Tier counts, district rollup and how much of the city hotspots cover.

    Coverage is the honesty check: if hotspots cover most of the population, the
    finding is "everything is a hotspot", and that should be visible here rather
    than buried in a long list.
    """
    repo = IntelligenceRepository(db)
    window = window_days or settings.trend_windows_days[0]
    rows = repo.list_hotspots(window_days=window)

    results = [
        hotspot_service.HotspotResult(
            district=row.district,
            latitude=row.centroid_latitude,
            longitude=row.centroid_longitude,
            ward_codes=list(row.ward_codes or []),
            sectors=list(row.sectors or []),
            population=row.population,
            total_complaints=row.total_complaints,
            critical_complaints=row.critical_complaints,
            mean_intensity=row.mean_intensity,
            peak_intensity=row.peak_intensity,
            peak_severity=row.peak_severity,
            intensity_z_score=row.intensity_z_score,
            tier=row.tier,
            window_days=row.window_days,
            ward_count=len(row.ward_codes or []),
        )
        for row in rows
    ]
    wards = analytics_service.build_ward_signals(repo, window_days=window)
    return HotspotSummaryOut(
        window_days=window,
        hotspot_count=len(results),
        tiers=hotspot_service.summarise_tiers(results),
        districts=hotspot_service.district_rollup(results),
        coverage=hotspot_service.coverage_note(results, wards),
    )


# --- trends ------------------------------------------------------------------
def _trend_out(row) -> TrendOut:
    return TrendOut(
        ward_code=row.ward_code,
        district=row.district,
        sector=row.sector,
        window_days=row.window_days,
        direction=row.direction,
        first_count=row.first_count,
        last_count=row.last_count,
        sample_count=row.sample_count,
        pct_change=row.pct_change,
        slope_per_window=row.slope_per_window,
        momentum=row.momentum,
        volatility=row.volatility,
        series=list(row.series or []),
        computed_at=row.computed_at,
    )


@trend_router.get("", response_model=TrendListOut)
def list_trends(
    direction: Optional[str] = Query(None, description="WORSENING | IMPROVING | STABLE | UNKNOWN"),
    ward_code: Optional[str] = None,
    sector: Optional[str] = None,
    window_days: Optional[int] = Query(None, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> TrendListOut:
    """Per-ward-sector trends, largest change first."""
    if direction and direction not in {value.value for value in TrendDirection}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown direction: {direction}")
    repo = IntelligenceRepository(db)
    rows = repo.list_trends(
        direction=direction,
        ward_code=ward_code,
        sector=sector,
        window_days=window_days,
        limit=limit,
    )
    return TrendListOut(count=len(rows), trends=[_trend_out(row) for row in rows])


@trend_router.get("/summary", response_model=TrendSummaryOut)
def trend_summary(
    sector: Optional[str] = None,
    window_days: Optional[int] = Query(None, ge=1, le=365),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> TrendSummaryOut:
    """Direction mix, per-sector breakdown and the fastest-worsening list."""
    repo = IntelligenceRepository(db)
    rows = repo.list_trends(sector=sector, window_days=window_days)
    results = [
        trend_service.TrendResult(
            ward_code=row.ward_code,
            sector=row.sector,
            window_days=row.window_days,
            direction=row.direction,
            first_count=row.first_count,
            last_count=row.last_count,
            sample_count=row.sample_count,
            pct_change=row.pct_change,
            slope_per_window=row.slope_per_window,
            momentum=row.momentum,
            volatility=row.volatility,
            series=list(row.series or []),
        )
        for row in rows
    ]
    emerging = repo.list_trends(direction=TrendDirection.WORSENING.value, window_days=window_days)
    emerging_rows = [_trend_out(row) for row in emerging[:limit]]
    return TrendSummaryOut(
        directions=trend_service.summarise_directions(results),
        sectors=trend_service.sector_trend_matrix(results),
        mean_slope=trend_service.average_slope(results),
        emerging_sectors=emerging_rows,
    )


@trend_router.get("/wards/{ward_code}", response_model=TrendListOut)
def ward_trends(
    ward_code: str,
    window_days: Optional[int] = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
) -> TrendListOut:
    """Every sector trend for one ward."""
    repo = IntelligenceRepository(db)
    rows = repo.list_trends(ward_code=ward_code, window_days=window_days)
    return TrendListOut(count=len(rows), trends=[_trend_out(row) for row in rows])


# --- risks -------------------------------------------------------------------
def _risk_out(row) -> RiskOut:
    return RiskOut(
        risk_code=row.risk_code,
        risk_type=row.risk_type,
        rule=row.rule,
        ward_code=row.ward_code,
        district=row.district,
        sector=row.sector,
        severity=row.severity,
        confidence=row.confidence,
        title=row.title,
        description=row.description,
        evidence=dict(row.evidence or {}),
        status=row.status,
        detected_at=row.detected_at,
    )


@risk_router.get("", response_model=RiskListOut)
def list_risks(
    risk_type: Optional[str] = None,
    severity: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    ward_code: Optional[str] = None,
    district: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> RiskListOut:
    """Detected risks, most severe and most confident first."""
    repo = IntelligenceRepository(db)
    rows = repo.list_risks(
        risk_type=risk_type,
        severity=severity,
        status=status_filter,
        ward_code=ward_code,
        district=district,
        limit=limit,
    )
    return RiskListOut(count=len(rows), risks=[_risk_out(row) for row in rows])


@risk_router.get("/summary", response_model=RiskSummaryOut)
def risk_summary(db: Session = Depends(get_db)) -> RiskSummaryOut:
    """Counts by type and severity, including how many remain open."""
    repo = IntelligenceRepository(db)
    rows = repo.list_risks()
    signals = [
        risk_service.RiskSignal(
            risk_type=row.risk_type,
            rule=row.rule,
            ward_code=row.ward_code,
            district=row.district,
            sector=row.sector,
            severity=row.severity,
            confidence=row.confidence,
            title=row.title,
            description=row.description,
            evidence=dict(row.evidence or {}),
        )
        for row in rows
    ]
    summary = risk_service.summarise_risks(signals)
    return RiskSummaryOut(
        total=len(rows),
        open=repo.open_risk_count(),
        by_type=summary["by_type"],
        by_severity=summary["by_severity"],
        confirmatory=len(risk_service.confirmatory_signals(signals)),
    )


@risk_router.patch("/{risk_code}/status", response_model=RiskOut)
def update_risk_status(
    risk_code: str, payload: RiskStatusIn, db: Session = Depends(get_db)
) -> RiskOut:
    """Acknowledge, dismiss or resolve a risk.

    A later analytics run will not undo this: risks are keyed by a stable code
    and only their evidence is refreshed.
    """
    repo = IntelligenceRepository(db)
    row = repo.set_risk_status(risk_code, payload.status)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown risk_code: {risk_code}")
    db.commit()
    return _risk_out(row)


@risk_router.get("/wards/{ward_code}", response_model=RiskListOut)
def ward_risks(ward_code: str, db: Session = Depends(get_db)) -> RiskListOut:
    """Every open risk for one ward, whatever its type."""
    repo = IntelligenceRepository(db)
    rows = repo.list_risks(ward_code=ward_code, status=RiskStatus.OPEN.value)
    return RiskListOut(count=len(rows), risks=[_risk_out(row) for row in rows])


# --- operations --------------------------------------------------------------
@ops_router.post("/sync", response_model=SyncOut)
def sync_from_mesh(
    district: Optional[list[str]] = Query(None, description="Restrict to these districts"),
    db: Session = Depends(get_db),
) -> SyncOut:
    """Copy the mesh's current state into local snapshots.

    Idempotent: unchanged mesh data inserts nothing, so this is safe to call on
    a schedule.
    """
    result = analytics_service.sync_from_mesh(db, districts=district)
    if not result.mesh_available:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "mesh database unavailable - run Module 4's seed_mesh.py first",
        )
    return SyncOut(**result.as_dict())


@ops_router.post("/analyse", response_model=AnalysisOut)
def run_analysis(
    window_days: Optional[int] = Query(None, ge=1, le=365),
    sector: Optional[list[str]] = Query(None),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    """Recompute hotspots, trends and risks from the stored snapshots."""
    result = analytics_service.analyse(db, window_days=window_days, sectors=sector)
    return AnalysisOut(**result.as_dict())


@ops_router.post("/refresh", response_model=dict)
def sync_and_analyse(
    district: Optional[list[str]] = Query(None),
    window_days: Optional[int] = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
) -> dict:
    """Sync from the mesh and immediately analyse, in one call."""
    return analytics_service.sync_and_analyse(db, districts=district, window_days=window_days)


@ops_router.get("/runs", response_model=list[RunOut])
def list_runs(limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)) -> list[RunOut]:
    """Recent analytics runs, newest first."""
    repo = IntelligenceRepository(db)
    return [
        RunOut(
            run_id=row.id,
            run_at=row.run_at,
            kind=row.kind,
            window_days=row.window_days,
            wards_analysed=row.wards_analysed,
            hotspots_created=row.hotspots_created,
            trends_computed=row.trends_computed,
            risks_detected=row.risks_detected,
            duration_ms=row.duration_ms,
        )
        for row in repo.list_runs(limit=limit)
    ]


# --- geography ---------------------------------------------------------------
@hotspot_router.get("/wards/{ward_code}/brief", response_model=WardBriefOut)
def ward_brief(ward_code: str, db: Session = Depends(get_db)) -> WardBriefOut:
    """Demand profile for one ward, for a ward-level detail view."""
    repo = IntelligenceRepository(db)
    location = next(
        (ward for ward in repo.list_ward_locations() if ward.ward_code == ward_code), None
    )
    if location is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown ward_code: {ward_code}")

    signal = next(
        (
            signal
            for signal in analytics_service.build_ward_signals(
                repo, window_days=settings.trend_windows_days[0]
            )
            if signal.ward_code == ward_code
        ),
        None,
    )
    return WardBriefOut(
        ward_code=location.ward_code,
        name=location.name,
        district=location.district,
        latitude=location.latitude,
        longitude=location.longitude,
        population=location.population,
        complaints=signal.complaints if signal else 0,
        critical_complaints=signal.critical_complaints if signal else 0,
        intensity=signal.intensity if signal else 0.0,
        sectors=signal.sectors if signal else [],
    )