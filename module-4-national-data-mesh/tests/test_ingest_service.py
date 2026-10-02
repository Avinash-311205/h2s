"""Tests for Module 3 -> Module 4 ingestion and lineage.

The gap score is computed from ``citizen_demand``, an aggregate that discards
the submissions behind it. These tests pin the behaviour that keeps the score
auditable: every accepted request leaves a request-level lineage row, and a
redelivered event does not double-count.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.enums import Sector
from app.database.connection import get_db
from app.main import app
from app.models.mesh_tables import CitizenDemand, CivicRecordLineage
from app.repositories.mesh_repository import MeshRepository
from app.services.ingest_service import CivicRecordIngestService, sector_for_category
from tests.conftest import make_ward


def envelope(
    request_id: str = "REQ-2026-000042",
    *,
    event_id: str = "evt-m3-0001",
    ward: Optional[str] = "Chennai Ward 1",
    category: str = "WATER",
    severity: int = 4,
    quality_status: str = "PASS",
    **payload_overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "description": "No water supply for four days",
        "ward": ward,
        "district": "Chennai",
        "latitude": 13.08,
        "longitude": 80.27,
        "category": category,
        "sub_category": "water_supply",
        "severity": severity,
        "language": "en",
        "data_quality_score": 0.9,
        "data_quality_status": quality_status,
    }
    payload.update(payload_overrides)
    return {
        "event_id": event_id,
        "event": "CIVIC_RECORD_PROCESSED",
        "event_type": "CIVIC_RECORD_PROCESSED",
        "channel": "civic.records.processed",
        "schema_version": "1.0",
        "source_module": "module-3-civic-data-processing",
        "correlation_id": request_id,
        "request_id": request_id,
        "issue_group_id": "group-1",
        "timestamp": "2026-01-15T10:00:00+00:00",
        "payload": payload,
    }


@pytest.fixture()
def ward(db: Session) -> None:
    MeshRepository(db).upsert_ward(
        ward_code="CHN-01",
        name="Chennai Ward 1",
        district="Chennai",
        state="Tamil Nadu",
        centroid_latitude=13.08,
        centroid_longitude=80.27,
        min_latitude=13.055,
        max_latitude=13.105,
        min_longitude=80.245,
        max_longitude=80.295,
        population=20_000,
        households=4_500,
        source="test",
    )
    db.commit()


# --- taxonomy -----------------------------------------------------------------
class TestSectorMapping:
    @pytest.mark.parametrize(
        "category",
        [sector.value for sector in Sector],
    )
    def test_known_category_maps_to_its_own_sector(self, category: str) -> None:
        # Module 2's Category taxonomy is intentionally identical to Sector.
        assert sector_for_category(category) == category

    def test_category_is_case_insensitive(self) -> None:
        assert sector_for_category("water") == Sector.WATER.value

    @pytest.mark.parametrize("category", ["TELEPATHY", "", None, "quantum"])
    def test_unknown_category_falls_back_to_unknown(self, category: Optional[str]) -> None:
        # Never guess a sector a citizen did not report.
        assert sector_for_category(category) == Sector.UNKNOWN.value


# --- ingestion ----------------------------------------------------------------
class TestIngestService:
    def test_creates_lineage_and_increments_demand(self, db: Session, ward: None) -> None:
        result = CivicRecordIngestService(db).ingest(envelope())

        assert result.accepted is True
        assert result.duplicate is False
        assert result.ward_code == "CHN-01"
        assert result.sector == Sector.WATER.value
        assert result.counted_in_demand is True
        assert result.complaint_count == 1

        lineage = MeshRepository(db).get_lineage("REQ-2026-000042")
        assert lineage is not None
        assert lineage.request_id == "REQ-2026-000042"
        assert lineage.source_event_id == "evt-m3-0001"
        assert lineage.ward_code == "CHN-01"
        assert lineage.category == Sector.WATER.value
        assert lineage.severity == 4
        assert lineage.counted_in_demand is True
        assert lineage.source_module == "module-3-civic-data-processing"

        demand = db.query(CitizenDemand).one()
        assert demand.ward_code == "CHN-01"
        assert demand.sector == Sector.WATER.value
        assert demand.complaint_count == 1
        assert demand.avg_severity == 4.0
        assert demand.max_severity == 4

    def test_lineage_retains_the_payload_it_was_built_from(self, db: Session, ward: None) -> None:
        CivicRecordIngestService(db).ingest(envelope())

        lineage = MeshRepository(db).get_lineage("REQ-2026-000042")
        assert lineage.payload["description"] == "No water supply for four days"

    def test_replay_is_idempotent(self, db: Session, ward: None) -> None:
        service = CivicRecordIngestService(db)
        service.ingest(envelope())
        result = service.ingest(envelope())

        assert result.duplicate is True
        assert result.reason == "duplicate_refreshed"
        # The aggregate must not absorb the replay.
        assert db.query(CitizenDemand).one().complaint_count == 1
        assert db.query(CivicRecordLineage).count() == 1

    def test_distinct_requests_accumulate(self, db: Session, ward: None) -> None:
        service = CivicRecordIngestService(db)
        service.ingest(envelope("REQ-2026-000042", event_id="evt-1", severity=2))
        service.ingest(envelope("REQ-2026-000043", event_id="evt-2", severity=4))

        demand = db.query(CitizenDemand).one()
        assert demand.complaint_count == 2
        assert demand.avg_severity == 3.0
        assert demand.max_severity == 4
        # Both submissions remain individually auditable.
        assert db.query(CivicRecordLineage).count() == 2

    def test_rejected_record_is_kept_for_provenance_but_not_counted(
        self, db: Session, ward: None
    ) -> None:
        result = CivicRecordIngestService(db).ingest(
            envelope(quality_status="REJECTED")
        )

        assert result.accepted is True
        assert result.counted_in_demand is False
        # Kept, so a reviewer can see why it was excluded...
        lineage = MeshRepository(db).get_lineage("REQ-2026-000042")
        assert lineage is not None
        assert lineage.counted_in_demand is False
        assert lineage.data_quality_status == "REJECTED"
        # ...but it must not inflate the gap score.
        assert db.query(CitizenDemand).count() == 0

    def test_record_without_coordinates_is_kept_without_a_ward(
        self, db: Session, ward: None
    ) -> None:
        result = CivicRecordIngestService(db).ingest(
            envelope(ward=None, latitude=None, longitude=None)
        )

        assert result.accepted is True
        assert result.ward_code is None
        assert result.counted_in_demand is False
        lineage = MeshRepository(db).get_lineage("REQ-2026-000042")
        assert lineage.location_status == "NO_COORDINATES"
        assert db.query(CitizenDemand).count() == 0

    def test_unknown_ward_label_falls_back_to_coordinates(self, db: Session, ward: None) -> None:
        result = CivicRecordIngestService(db).ingest(envelope(ward="Nowhere Land"))

        assert result.ward_code == "CHN-01"

    def test_severity_is_clamped(self, db: Session, ward: None) -> None:
        service = CivicRecordIngestService(db)
        service.ingest(envelope("REQ-2026-000042", severity=99))
        service.ingest(envelope("REQ-2026-000043", event_id="evt-2", severity=-4))

        demand = db.query(CitizenDemand).one()
        assert demand.max_severity == 5

    def test_correlation_id_is_authoritative_when_payload_disagrees(
        self, db: Session, ward: None
    ) -> None:
        bad = envelope()
        bad["payload"]["request_id"] = "REQ-WRONG"

        CivicRecordIngestService(db).ingest(bad)

        assert MeshRepository(db).get_lineage("REQ-2026-000042") is not None

    @pytest.mark.parametrize(
        "broken",
        [
            {"correlation_id": "", "request_id": "", "payload": {}},
            {"payload": "not-a-dict"},
            "not-an-object",
        ],
    )
    def test_unusable_events_are_rejected_not_ingested(
        self, db: Session, ward: None, broken: Any
    ) -> None:
        result = CivicRecordIngestService(db).ingest(broken)

        assert result.accepted is False
        assert db.query(CivicRecordLineage).count() == 0


# --- consumer -----------------------------------------------------------------
class TestConsumer:
    def test_consumer_ingests_a_raw_redis_message(
        self, db: Session, ward: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.events.consumer as consumer_module

        monkeypatch.setattr(
            consumer_module,
            "SessionLocal",
            sessionmaker(bind=db.get_bind()),
        )

        consumer = consumer_module.CivicRecordConsumer()
        request_id = consumer.handle(json.dumps(envelope()))

        assert request_id == "REQ-2026-000042"
        assert consumer.ingested == 1
        assert MeshRepository(db).get_lineage(request_id) is not None

    def test_consumer_counts_a_replay_as_a_duplicate(
        self, db: Session, ward: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.events.consumer as consumer_module

        monkeypatch.setattr(
            consumer_module,
            "SessionLocal",
            sessionmaker(bind=db.get_bind()),
        )

        consumer = consumer_module.CivicRecordConsumer()
        consumer.handle(json.dumps(envelope()))
        consumer.handle(json.dumps(envelope()))

        assert consumer.duplicates == 1
        assert db.query(CitizenDemand).one().complaint_count == 1

    def test_consumer_ignores_malformed_json(self, db: Session, ward: None) -> None:
        import app.events.consumer as consumer_module

        consumer = consumer_module.CivicRecordConsumer()
        assert consumer.handle("{not json") is None
        assert consumer.rejected == 0


# --- HTTP surface -------------------------------------------------------------
class TestIngestRoutes:
    @pytest.fixture()
    def client(self, db: Session) -> TestClient:
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.pop(get_db, None)

    def test_ingest_endpoint_reports_what_it_did(
        self, client: TestClient, ward: None
    ) -> None:
        response = client.post("/api/v1/ingest/civic-records", json=envelope())

        assert response.status_code == 202
        body = response.json()
        assert body["request_id"] == "REQ-2026-000042"
        assert body["ward_code"] == "CHN-01"
        assert body["sector"] == Sector.WATER.value
        assert body["counted_in_demand"] is True
        assert body["complaint_count"] == 1

    def test_lineage_endpoint_returns_the_request(
        self, client: TestClient, db: Session, ward: None
    ) -> None:
        client.post("/api/v1/ingest/civic-records", json=envelope())

        response = client.get("/api/v1/lineage/REQ-2026-000042")

        assert response.status_code == 200
        body = response.json()
        assert body["source_event_id"] == "evt-m3-0001"
        assert body["ward_code"] == "CHN-01"
        assert body["sector"] == Sector.WATER.value

    def test_lineage_endpoint_404s_for_an_unknown_request(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/v1/lineage/REQ-1970-000001")
        assert response.status_code == 404

    def test_ingest_endpoint_rejects_an_event_with_no_correlation_id(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/ingest/civic-records",
            json={"event": "CIVIC_RECORD_PROCESSED", "payload": {}},
        )
        assert response.status_code == 400


# --- audit queries ------------------------------------------------------------
class TestLineageQueries:
    def test_aggregate_can_be_audited_back_to_its_requests(
        self, db: Session, ward: None
    ) -> None:
        service = CivicRecordIngestService(db)
        service.ingest(envelope("REQ-2026-000042", event_id="evt-1"))
        service.ingest(envelope("REQ-2026-000043", event_id="evt-2"))

        demand = db.query(CitizenDemand).one()
        submissions = MeshRepository(db).list_lineage(
            ward_code=demand.ward_code, sector=demand.sector
        )

        # This is the join a policymaker needs: the aggregate says "23 water
        # complaints in CHN-01", and these are the actual requests behind it.
        assert [s.request_id for s in submissions] == ["REQ-2026-000043", "REQ-2026-000042"]
        assert all(s.ward_code == demand.ward_code and s.sector == demand.sector for s in submissions)
        assert {s.source_event_id for s in submissions} == {"evt-1", "evt-2"}

    def test_lineage_can_be_traced_by_issue_group(self, db: Session, ward: None) -> None:
        service = CivicRecordIngestService(db)
        service.ingest(envelope("REQ-2026-000042", event_id="evt-1"))
        other = envelope("REQ-2026-000043", event_id="evt-2")
        other["issue_group_id"] = "group-2"
        service.ingest(other)

        group = MeshRepository(db).list_lineage_for_request_group("group-1")
        assert [s.request_id for s in group] == ["REQ-2026-000042"]

    def test_lineage_can_be_traced_by_upstream_event_id(self, db: Session, ward: None) -> None:
        CivicRecordIngestService(db).ingest(envelope(event_id="evt-m3-0001"))

        found = MeshRepository(db).get_lineage_by_source_event("evt-m3-0001")
        assert found is not None and found.request_id == "REQ-2026-000042"
