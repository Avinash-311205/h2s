"""API tests covering the full request/response surface."""

from __future__ import annotations

from app.database.connection import SessionLocal
from app.models.civic_record import ProcessedCivicRecord


def test_health_check(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["service"] == "civic-data-processing"
    checks = body["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["geocoding_provider"]["active"] == "mock"
    assert checks["normalization_dictionary"]["keywords"] > 100
    assert checks["event_publisher"]["status"] in {"ok", "disabled"}
    # Every check reports a status so clients can render a consistent table.
    assert all("status" in check for check in checks.values())


def test_root_endpoint(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Civic Data Processing" in response.json()["module"]


def test_process_sample_request_end_to_end(client, sample_payload):
    response = client.post("/api/v1/civic/process", json=sample_payload)

    assert response.status_code == 201, response.text
    body = response.json()
    record = body["record"]

    assert body["idempotent"] is False
    assert body["event_type"] == "CIVIC_RECORD_PROCESSED"
    assert body["event_published"] is True

    assert record["request_id"] == "REQ-10023"
    assert record["category"] == "ROAD"
    assert record["sub_category"] == "DAMAGED_ROAD"
    assert record["language"] == "ta"
    assert record["severity"] == 4
    assert record["description"] == "இந்த சாலையில் நிறைய பள்ளங்கள் உள்ளன"
    assert record["data_quality_status"] == "VALID"
    assert record["data_quality_score"] >= 0.9
    assert record["processing_status"] == "PROCESSED"
    assert record["is_duplicate"] is False
    assert record["location"]["district"] == "Chengalpattu"
    assert record["location"]["state"] == "Tamil Nadu"
    assert record["location"]["country"] == "India"
    assert record["location_status"] == "RESOLVED"
    assert record["issue_group_id"].startswith("ISSUE-")
    assert record["raw_data"]["request_id"] == "REQ-10023"


def test_get_record_and_status(client, sample_payload):
    client.post("/api/v1/civic/process", json=sample_payload)

    record_response = client.get("/api/v1/civic/REQ-10023")
    status_response = client.get("/api/v1/civic/REQ-10023/status")

    assert record_response.status_code == 200
    assert status_response.status_code == 200

    status = status_response.json()
    assert status["processing_status"] == "PROCESSED"
    assert status["data_quality_status"] == "VALID"
    assert status["reprocess_count"] == 0
    assert len(status["events"]) == 1
    assert status["events"][0]["event_type"] == "CIVIC_RECORD_PROCESSED"
    assert status["events"][0]["status"] == "PUBLISHED"


def test_get_issue_group_and_member_requests(client, sample_payload):
    created = client.post("/api/v1/civic/process", json=sample_payload).json()
    issue_group_id = created["record"]["issue_group_id"]

    group_response = client.get(f"/api/v1/issues/{issue_group_id}")
    members_response = client.get(f"/api/v1/issues/{issue_group_id}/requests")

    assert group_response.status_code == 200
    group = group_response.json()
    assert group["issue_group_id"] == issue_group_id
    assert group["category"] == "ROAD"
    assert group["sub_category"] == "DAMAGED_ROAD"
    assert group["report_count"] == 1
    assert group["status"] == "OPEN"
    assert group["district"] == "Chengalpattu"

    members = members_response.json()
    assert len(members) == 1
    assert members[0]["request_id"] == "REQ-10023"


def test_duplicate_reports_share_an_issue_group_but_stay_stored(client):
    base = {
        "language": "en",
        "category": "transport",
        "sub_category": "bridge",
        "severity": 4,
        "location": {"latitude": 12.9249, "longitude": 80.1},
        "created_at": "2026-09-13T10:30:00Z",
    }

    first = client.post(
        "/api/v1/civic/process",
        json={**base, "request_id": "REQ-20001", "description": "Bridge damaged"},
    ).json()
    second = client.post(
        "/api/v1/civic/process",
        json={**base, "request_id": "REQ-20002", "description": "Bridge needs repair"},
    ).json()

    assert first["record"]["is_duplicate"] is False
    assert second["record"]["is_duplicate"] is True
    assert first["record"]["issue_group_id"] == second["record"]["issue_group_id"]

    group = client.get(f"/api/v1/issues/{first['record']['issue_group_id']}").json()
    assert group["report_count"] == 2

    # Both citizen requests remain individually retrievable.
    assert client.get("/api/v1/civic/REQ-20001").status_code == 200
    assert client.get("/api/v1/civic/REQ-20002").status_code == 200
    assert len(client.get(f"/api/v1/issues/{group['issue_group_id']}/requests").json()) == 2


def test_invalid_coordinates_are_stored_as_invalid_not_rejected(client):
    payload = {
        "request_id": "REQ-30001",
        "language": "en",
        "category": "water",
        "sub_category": "drinking water",
        "description": "no drinking water",
        "severity": 3,
        "location": {"latitude": 91, "longitude": 200},
        "created_at": "2026-09-13T10:30:00Z",
    }

    response = client.post("/api/v1/civic/process", json=payload)

    assert response.status_code == 201, response.text
    record = response.json()["record"]
    assert record["data_quality_status"] == "INVALID"
    assert record["processing_status"] == "REJECTED"
    assert record["location"]["latitude"] is None  # never written to spatial columns
    assert record["raw_data"]["location"]["latitude"] == 91  # but preserved raw

    assert client.get("/api/v1/civic/REQ-30001").status_code == 200


def test_missing_fields_are_flagged_but_stored(client):
    response = client.post("/api/v1/civic/process", json={"request_id": "REQ-30002"})

    assert response.status_code == 201
    record = response.json()["record"]
    assert record["data_quality_status"] == "INVALID"
    assert record["processing_status"] == "REJECTED"
    assert any(issue["code"] == "MISSING_LOCATION" for issue in response.json()["errors"])
    assert client.get("/api/v1/civic/REQ-30002").status_code == 200


def test_location_text_only_requests_are_processed(client):
    payload = {
        "request_id": "REQ-30003",
        "language": "en",
        "category": "water",
        "sub_category": "water supply problem",
        "description": "water supply problem in the village",
        "severity": 3,
        "location": {"location_text": "Chengalpattu, Tamil Nadu"},
        "created_at": "2026-09-13T10:30:00Z",
    }

    response = client.post("/api/v1/civic/process", json=payload)

    assert response.status_code == 201
    record = response.json()["record"]
    assert record["category"] == "WATER"
    assert record["sub_category"] == "DRINKING_WATER"
    assert record["location"]["district"] == "Chengalpattu"
    assert record["data_quality_status"] in {"VALID", "NEEDS_REVIEW"}


def test_reprocess_endpoint_updates_the_existing_record(client, sample_payload):
    original = client.post("/api/v1/civic/process", json=sample_payload).json()

    response = client.post("/api/v1/civic/reprocess/REQ-10023")

    assert response.status_code == 200
    body = response.json()
    assert body["record"]["request_id"] == "REQ-10023"
    assert body["record"]["reprocess_count"] == 1
    assert body["record"]["category"] == "ROAD"
    assert body["record"]["issue_group_id"] == original["record"]["issue_group_id"]

    with SessionLocal() as session:
        assert session.query(ProcessedCivicRecord).count() == 1


def test_reprocess_unknown_request_returns_404(client):
    response = client.post("/api/v1/civic/reprocess/REQ-DOES-NOT-EXIST")

    assert response.status_code == 404


def test_unknown_request_returns_404(client):
    assert client.get("/api/v1/civic/REQ-MISSING").status_code == 404
    assert client.get("/api/v1/civic/REQ-MISSING/status").status_code == 404
    assert client.get("/api/v1/issues/ISSUE-MISSING").status_code == 404


def test_oversized_payload_is_rejected(client):
    payload = {
        "request_id": "REQ-40001",
        "description": "x" * 400_000,
    }

    response = client.post("/api/v1/civic/process", json=payload)

    assert response.status_code == 413


def test_malformed_payload_returns_safe_400(client):
    response = client.post("/api/v1/civic/process", json={"request_id": 12345})

    assert response.status_code == 400
    assert "detail" in response.json()


def test_event_retry_endpoint_reports_outbox_state(client, sample_payload):
    client.post("/api/v1/civic/process", json=sample_payload)

    response = client.post("/api/v1/events/retry")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"attempted", "published", "failed", "pending_before"}

    pending = client.get("/api/v1/events/pending").json()
    assert pending["count"] == 0  # the noop transport accepted everything
