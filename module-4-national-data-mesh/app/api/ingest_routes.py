"""HTTP routes for Module 3 -> Module 4 ingestion.

The mesh is normally fed by the Redis subscriber in ``app.events.consumer``.
These routes exist so the same ingestion path can be exercised on demand --
by a test, a replay tool, or an operator investigating a failed delivery --
without a second implementation of the logic.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.connection import get_db
from app.repositories.mesh_repository import MeshRepository
from app.services.ingest_service import CivicRecordIngestService

logger = get_logger(__name__)

ingest_router = APIRouter(tags=["ingestion"])


class CivicRecordEventIn(BaseModel):
    """The envelope Module 3 publishes on ``civic.records.processed``."""

    event_id: Optional[str] = None
    event: Optional[str] = "CIVIC_RECORD_PROCESSED"
    event_type: Optional[str] = None
    correlation_id: Optional[str] = None
    request_id: Optional[str] = None
    issue_group_id: Optional[str] = None
    schema_version: Optional[str] = None
    source_module: Optional[str] = None
    timestamp: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class LineageOut(BaseModel):
    request_id: str
    source_event_id: Optional[str] = None
    ward_code: Optional[str] = None
    sector: str
    category: str
    severity: int
    data_quality_status: Optional[str] = None
    counted_in_demand: bool
    received_at: str


class IngestOut(BaseModel):
    request_id: str
    accepted: bool
    reason: str
    duplicate: bool
    ward_code: Optional[str] = None
    sector: str
    category: str
    severity: int
    counted_in_demand: bool
    complaint_count: Optional[int] = None


@ingest_router.post(
    "/ingest/civic-records",
    response_model=IngestOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a Module 3 civic record into the mesh",
)
def ingest_civic_record(envelope: CivicRecordEventIn, db: Session = Depends(get_db)) -> IngestOut:
    """Apply one Module 3 event to ``citizen_demand`` and ``civic_record_lineage``.

    Idempotent on ``request_id``: a replay refreshes the lineage row and does not
    add a second complaint to the aggregate.
    """
    payload = envelope.model_dump(exclude_none=False)
    result = CivicRecordIngestService(db).ingest(payload)
    if not result.accepted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=result.reason
        )
    return IngestOut(
        request_id=result.request_id,
        accepted=result.accepted,
        reason=result.reason,
        duplicate=result.duplicate,
        ward_code=result.ward_code,
        sector=result.sector,
        category=result.category,
        severity=result.severity,
        counted_in_demand=result.counted_in_demand,
        complaint_count=result.complaint_count,
    )


@ingest_router.get(
    "/lineage",
    response_model=list[LineageOut],
    summary="Individual submissions behind a ward/sector aggregate",
)
def list_lineage(
    ward_code: Optional[str] = None,
    sector: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[LineageOut]:
    """List the citizen requests folded into a demand aggregate.

    This is the audit half of the pair: ``/demand?ward_code=..&sector=..`` says
    how many complaints a ward has, this says who reported them.
    """
    rows = MeshRepository(db).list_lineage(
        ward_code=ward_code, sector=sector, category=category, limit=limit
    )
    return [
        LineageOut(
            request_id=row.request_id,
            source_event_id=row.source_event_id,
            ward_code=row.ward_code,
            sector=row.sector,
            category=row.category,
            severity=row.severity,
            data_quality_status=row.data_quality_status,
            counted_in_demand=row.counted_in_demand,
            received_at=row.received_at.isoformat() if row.received_at else "",
        )
        for row in rows
    ]


@ingest_router.get("/lineage/{request_id}", response_model=LineageOut, summary="Lineage for one request")
def get_lineage(request_id: str, db: Session = Depends(get_db)) -> LineageOut:
    """Trace one citizen submission through the pipeline.

    Answers "which requests produced this gap?" for a given ``request_id``.
    """
    record = MeshRepository(db).get_lineage(request_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no lineage recorded for {request_id}",
        )
    return LineageOut(
        request_id=record.request_id,
        source_event_id=record.source_event_id,
        ward_code=record.ward_code,
        sector=record.sector,
        category=record.category,
        severity=record.severity,
        data_quality_status=record.data_quality_status,
        counted_in_demand=record.counted_in_demand,
        received_at=record.received_at.isoformat() if record.received_at else "",
    )
