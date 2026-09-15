"""Idempotency: the same request_id must never produce two civic records."""

from __future__ import annotations

from app.database.connection import SessionLocal
from app.models.citizen_request import CitizenRequest
from app.models.civic_record import ProcessedCivicRecord
from app.models.event_outbox import EventOutbox
from app.models.issue_group import IssueGroup


def counts() -> dict[str, int]:
    with SessionLocal() as session:
        return {
            "citizen_requests": session.query(CitizenRequest).count(),
            "processed_records": session.query(ProcessedCivicRecord).count(),
            "issue_groups": session.query(IssueGroup).count(),
            "events": session.query(EventOutbox).count(),
        }


def test_same_request_twice_creates_one_processed_record(client, sample_payload):
    first = client.post("/api/v1/civic/process", json=sample_payload)
    second = client.post("/api/v1/civic/process", json=sample_payload)

    assert first.status_code == 201
    assert second.status_code == 200

    assert first.json()["idempotent"] is False
    assert second.json()["idempotent"] is True
    assert second.json()["payload_changed"] is False

    assert second.json()["record"]["issue_group_id"] == first.json()["record"]["issue_group_id"]
    assert counts() == {
        "citizen_requests": 1,
        "processed_records": 1,
        "issue_groups": 1,
        "events": 1,
    }


def test_repeated_delivery_is_stable_over_many_attempts(client, sample_payload):
    for _ in range(5):
        client.post("/api/v1/civic/process", json=sample_payload)

    assert counts()["processed_records"] == 1
    assert counts()["issue_groups"] == 1


def test_conflicting_payload_for_same_request_id_keeps_the_original(client, sample_payload):
    client.post("/api/v1/civic/process", json=sample_payload)

    conflicting = {**sample_payload, "category": "water", "sub_category": "drinking water",
                   "description": "no drinking water at all"}
    response = client.post("/api/v1/civic/process", json=conflicting)

    assert response.status_code == 200
    body = response.json()
    assert body["idempotent"] is True
    assert body["payload_changed"] is True
    # The first processing result is authoritative.
    assert body["record"]["category"] == "ROAD"
    assert body["record"]["sub_category"] == "DAMAGED_ROAD"
    assert counts()["processed_records"] == 1


def test_reprocess_is_idempotent_and_does_not_inflate_report_count(client, sample_payload):
    created = client.post("/api/v1/civic/process", json=sample_payload).json()
    issue_group_id = created["record"]["issue_group_id"]

    client.post("/api/v1/civic/reprocess/REQ-10023")
    second = client.post("/api/v1/civic/reprocess/REQ-10023").json()

    assert second["record"]["issue_group_id"] == issue_group_id
    assert second["record"]["reprocess_count"] == 2
    assert counts()["processed_records"] == 1

    group = client.get(f"/api/v1/issues/{issue_group_id}").json()
    assert group["report_count"] == 1


def test_reprocess_after_logic_still_groups_with_other_reports(client, sample_payload):
    created = client.post("/api/v1/civic/process", json=sample_payload).json()
    issue_group_id = created["record"]["issue_group_id"]

    # A second, different citizen report (same language, same place) joins the group.
    client.post(
        "/api/v1/civic/process",
        json={**sample_payload, "request_id": "REQ-10024", "description": "சாலையில் நிறைய பள்ளங்கள் உள்ளன"},
    )
    assert client.get(f"/api/v1/issues/{issue_group_id}").json()["report_count"] == 2

    # Reprocessing the first record keeps the group and the count intact.
    reprocessed = client.post("/api/v1/civic/reprocess/REQ-10023").json()

    assert reprocessed["record"]["issue_group_id"] == issue_group_id
    assert client.get(f"/api/v1/issues/{issue_group_id}").json()["report_count"] == 2
