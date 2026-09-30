"""HTTP routes for Module 4 - National Data Mesh.

Routers are kept separate by purpose so the app can mount them independently:
health for probes, mesh for domain data, gaps for analysis, investment for
delivery. Every read is read-only; the only writes are the gap-analysis run
and the catalogue refresh.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    AssetOut,
    CapacityTargetsOut,
    DataProductOut,
    DemandTotalOut,
    DistrictSummaryOut,
    DomainQualityOut,
    GapOut,
    HealthOut,
    InvestmentSummaryOut,
    LocateIn,
    ProjectOut,
    RunAnalysisIn,
    RunAnalysisOut,
    WardAssignmentOut,
    WardOut,
)
from app.core.config import settings
from app.core.enums import DataDomain
from app.core.logging import get_logger
from app.core.utils import safe_div, utcnow
from app.database.connection import get_db
from app.models.mesh_tables import GapRecord
from app.repositories.mesh_repository import MeshRepository
from app.services import (
    capacity_service,
    gap_service,
    geo_service,
    investment_service,
    mesh_service,
)

logger = get_logger(__name__)

health_router = APIRouter(tags=["health"])
mesh_router = APIRouter(tags=["mesh"])
gap_router = APIRouter(prefix="/gaps", tags=["gaps"])
investment_router = APIRouter(prefix="/investment", tags=["investment"])


# --- health ------------------------------------------------------------------
@health_router.get("/health", response_model=HealthOut, summary="Liveness and mesh row counts")
def health_check(db: Session = Depends(get_db)) -> HealthOut:
    """Ready only once GIS wards exist; ``degraded`` otherwise.

    Other domains can legitimately be empty, but a mesh with no wards has no
    join key, so nothing downstream can work.
    """
    counts = MeshRepository(db).counts()
    ready = counts["wards"] > 0
    return HealthOut(
        status="ok" if ready else "degraded",
        service=settings.service_name,
        version=settings.version,
        database_ready=ready,
        counts=counts,
        domains=[domain.value for domain in DataDomain],
    )


# --- catalogue and quality ---------------------------------------------------
@mesh_router.get("/catalog", response_model=list[DataProductOut], summary="Mesh catalogue with lineage")
def catalogue(db: Session = Depends(get_db)) -> list[DataProductOut]:
    """Registered data products with upstream sources and last-scored quality."""
    return [
        DataProductOut(
            product_key=product.product_key,
            name=product.name,
            domain=product.domain,
            description=product.description,
            upstream_sources=product.upstream_sources or [],
            record_count=product.record_count,
            completeness=product.completeness,
            validity=product.validity,
            timeliness=product.timeliness,
            quality_status=product.quality_status,
            quality_score=product.quality_score,
            generated_at=product.generated_at,
            refresh_interval_days=product.refresh_interval_days,
        )
        for product in MeshRepository(db).list_data_products()
    ]


@mesh_router.get("/quality", response_model=list[DomainQualityOut], summary="Live quality score per domain")
def quality(db: Session = Depends(get_db)) -> list[DomainQualityOut]:
    """Recompute domain quality from the data as it stands right now.

    Deliberately not read from the catalogue: a cached score would keep
    reporting HEALTHY after the underlying rows decayed.
    """
    repo = MeshRepository(db)
    verdicts = mesh_service.refresh_all(
        wards=repo.list_wards(),
        assets=repo.list_assets(),
        projects=repo.list_projects(),
        demand=repo.list_demand(),
    )
    return [DomainQualityOut(**verdict.as_dict()) for verdict in verdicts]


# --- geography ---------------------------------------------------------------
@mesh_router.get("/wards", response_model=list[WardOut], summary="List GIS wards")
def list_wards(
    district: Optional[str] = Query(None, max_length=120),
    state: Optional[str] = Query(None, max_length=120),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> list[WardOut]:
    wards = MeshRepository(db).list_wards(district=district, state=state)[:limit]
    return [
        WardOut(
            ward_code=ward.ward_code,
            name=ward.name,
            district=ward.district,
            state=ward.state,
            zone=ward.zone,
            centroid_latitude=ward.centroid_latitude,
            centroid_longitude=ward.centroid_longitude,
            population=ward.population,
            households=ward.households,
            area_km2=ward.area_km2,
        )
        for ward in wards
    ]


@mesh_router.get("/districts", response_model=list[DistrictSummaryOut], summary="District rollup")
def list_districts(db: Session = Depends(get_db)) -> list[DistrictSummaryOut]:
    return [DistrictSummaryOut(**row) for row in MeshRepository(db).list_districts()]


@mesh_router.post("/locate", response_model=WardAssignmentOut, summary="Assign a point to a ward")
def locate_point(payload: LocateIn, db: Session = Depends(get_db)) -> WardAssignmentOut:
    """Resolve a coordinate to a ward. Reads only - nothing is persisted."""
    assignment = geo_service.assign_to_ward(
        payload.latitude, payload.longitude, MeshRepository(db).list_wards()
    )
    return WardAssignmentOut(**assignment.as_dict())


# --- domain data -------------------------------------------------------------
@mesh_router.get("/assets", response_model=list[AssetOut], summary="List infrastructure assets")
def list_assets(
    ward_code: Optional[str] = Query(None, max_length=20),
    sector: Optional[str] = Query(None, max_length=40),
    status_filter: Optional[str] = Query(None, alias="status", max_length=20),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[AssetOut]:
    year = date.today().year
    assets = MeshRepository(db).list_assets(
        ward_code=ward_code, sector=sector, status=status_filter, limit=limit
    )
    return [
        AssetOut(
            asset_code=asset.asset_code,
            ward_code=asset.ward_code,
            asset_type=asset.asset_type,
            status=asset.status,
            condition=asset.condition,
            capacity_units=asset.capacity_units,
            installed_year=asset.installed_year,
            degraded=capacity_service.is_degraded(asset, year),
        )
        for asset in assets
    ]


@mesh_router.get("/assets/breakdown", summary="Asset counts by type and status")
def asset_breakdown(db: Session = Depends(get_db)) -> dict:
    return {"rows": MeshRepository(db).asset_type_breakdown()}


@mesh_router.get("/demand/totals", response_model=list[DemandTotalOut], summary="Complaint totals per sector")
def demand_totals(db: Session = Depends(get_db)) -> list[DemandTotalOut]:
    return [DemandTotalOut(**row) for row in MeshRepository(db).demand_totals_by_sector()]


@mesh_router.get("/capacity-targets", response_model=CapacityTargetsOut, summary="Coverage assumptions")
def capacity_targets() -> CapacityTargetsOut:
    """Expose the per-asset coverage assumptions, so scores can be audited.

    A gap score is only as credible as the numbers behind it; this endpoint
    exposes those numbers rather than burying them in the service layer.
    """
    return CapacityTargetsOut(
        asset_population_capacity=capacity_service.sector_coverage_targets(),
        sector_asset_types={
            sector: capacity_service.asset_types_for(sector)
            for sector in capacity_service.all_sectors()
        },
    )


# --- gaps --------------------------------------------------------------------
@gap_router.get("", response_model=list[GapOut], summary="Ranked infrastructure gaps")
def list_gaps(
    ward_code: Optional[str] = Query(None, max_length=20),
    sector: Optional[str] = Query(None, max_length=40),
    severity: Optional[str] = Query(None, max_length=20),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[GapOut]:
    """Gaps worst-first, each carrying the components behind its score."""
    records = MeshRepository(db).list_gaps(
        ward_code=ward_code, sector=sector, severity=severity, min_score=min_score, limit=limit
    )
    return [_gap_record_to_out(record) for record in records]


@gap_router.post("/analyse", response_model=RunAnalysisOut, summary="Recompute gaps across the mesh")
def run_analysis(payload: RunAnalysisIn, db: Session = Depends(get_db)) -> RunAnalysisOut:
    """Join every domain and recompute the gap table.

    Read-only apart from ``persist``, which stores the run so Module 5 can
    compare consecutive runs and detect which gaps are actually improving.
    """
    started = time.perf_counter()
    repo = MeshRepository(db)

    if not repo.list_wards():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="mesh is empty - load GIS wards before running analysis",
        )

    wards = repo.list_wards(district=payload.district)
    results = gap_service.run_gap_analysis(
        wards=wards,
        demand_by_ward=repo.demand_by_ward(),
        assets_by_ward=repo.assets_by_ward(),
        projects_by_ward=repo.projects_by_ward(),
        sectors=payload.sectors,
        reference_year=date.today().year,
    )

    if payload.persist:
        repo.replace_gaps(results)
        _refresh_catalogue(db, repo)

    by_severity: dict[str, int] = {}
    for result in results:
        by_severity[result.severity] = by_severity.get(result.severity, 0) + 1

    logger.info(
        "gap_analysis_complete",
        extra={
            "wards": len(wards),
            "gaps": len(results),
            "ms": int((time.perf_counter() - started) * 1000),
        },
    )

    return RunAnalysisOut(
        analysed_at=utcnow(),
        ward_count=len(wards),
        sector_count=len({result.sector for result in results}),
        gap_count=len(results),
        persisted=payload.persist,
        top_gaps=[_result_to_out(result) for result in results[:20]],
        by_severity=by_severity,
    )


# --- investment --------------------------------------------------------------
@investment_router.get("/projects", response_model=list[ProjectOut], summary="List projects")
def list_projects(
    ward_code: Optional[str] = Query(None, max_length=20),
    sector: Optional[str] = Query(None, max_length=40),
    status_filter: Optional[str] = Query(None, alias="status", max_length=20),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[ProjectOut]:
    out: list[ProjectOut] = []
    for project in MeshRepository(db).list_projects(
        ward_code=ward_code, sector=sector, status=status_filter, limit=limit
    ):
        out.append(
            ProjectOut(
                project_code=project.project_code,
                ward_code=project.ward_code,
                title=project.title,
                sector=project.sector,
                status=project.status,
                budget_lakhs=project.budget_lakhs,
                spent_lakhs=project.spent_lakhs,
                absorption_rate=round(
                    safe_div(project.spent_lakhs, project.budget_lakhs), 4
                ),
                beneficiaries=project.beneficiaries,
                delay_days=project.delay_days,
            )
        )
    return out


@investment_router.get("/summary", response_model=InvestmentSummaryOut, summary="Delivery health")
def investment_summary(
    ward_code: Optional[str] = Query(None, max_length=20),
    sector: Optional[str] = Query(None, max_length=40),
    db: Session = Depends(get_db),
) -> InvestmentSummaryOut:
    projects = MeshRepository(db).list_projects(ward_code=ward_code, sector=sector)
    summary = investment_service.summarise_investment(projects)
    payload = summary.as_dict()
    payload["delivery_score"] = investment_service.delivery_score(summary)
    return InvestmentSummaryOut(**payload)


# --- helpers -----------------------------------------------------------------
def _refresh_catalogue(db: Session, repo: MeshRepository) -> None:
    """Re-score every domain and upsert the catalogue entries."""
    verdicts = mesh_service.refresh_all(
        wards=repo.list_wards(),
        assets=repo.list_assets(),
        projects=repo.list_projects(),
        demand=repo.list_demand(),
    )
    for verdict in verdicts:
        repo.upsert_data_product(
            verdict,
            product_key=f"{verdict.domain.lower()}.primary",
            name=f"{verdict.domain.title()} primary dataset",
            description=mesh_service.PRODUCT_DESCRIPTIONS.get(verdict.domain, ""),
        )
    db.commit()


def _gap_record_to_out(record: GapRecord) -> GapOut:
    """Serialise a stored gap, including its components and evidence."""
    return GapOut(
        ward_code=record.ward_code,
        sector=record.sector,
        gap_score=round(record.gap_score, 2),
        severity=record.severity,
        components={
            "demand": round(record.demand_score, 4),
            "absence": round(record.absence_score, 4),
            "quality": round(record.quality_score, 4),
        },
        population=record.population,
        complaints=record.complaints,
        avg_severity=round(record.avg_severity, 3),
        asset_count=record.asset_count,
        functional_asset_count=record.functional_asset_count,
        served_population=round(record.served_population, 1),
        coverage_ratio=round(record.coverage_ratio, 4),
        committed_capex_lakhs=round(record.committed_capex_lakhs, 2),
        spent_capex_lakhs=round(record.spent_capex_lakhs, 2),
        active_project_count=record.active_project_count,
        recommended_action=record.recommended_action,
        rationale=record.rationale,
        evidence=record.evidence or {},
        computed_at=record.computed_at,
    )


def _result_to_out(result) -> GapOut:
    """Serialise a freshly computed gap (not yet persisted)."""
    return GapOut(**result.as_dict(), computed_at=utcnow())