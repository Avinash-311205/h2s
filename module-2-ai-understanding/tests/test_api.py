"""HTTP contract tests.

Module 3 and the dashboards consume these endpoints, so status codes, response
shapes and the idempotency guarantee are all pinned here.
"""

from __future__ import annotations

import io

from app.core.enums import SeverityBand


def _payload(request_id: str = "REQ-1001", text: str | None = None) -> dict:
    return {
        "request_id": request_id,
        "text": text
        or "No water supply for days in our colony near the temple, 560001",
        "source_channel": "MOBILE_APP",
    }


def test_health_check(client) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "healthy"}


def test_capabilities_advertises_offline_providers(client) -> None:
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["offline_capable"] is True

    # Every stage must report its real availability, and the stages that need no
    # model download must be live even with no heavy deps installed.
    stages = {stage["stage"]: stage for stage in body["stages"]}
    assert set(stages) == {"asr", "translation", "ner", "classification", "image_analysis"}
    assert stages["ner"]["available"] is True
    assert stages["classification"]["available"] is True
    assert stages["translation"]["available"] is True
    assert stages["image_analysis"]["available"] is True
    # Classification must report a real category inventory for the UI.
    assert stages["classification"]["detail"]["categories"] >= 10
    assert stages["classification"]["detail"]["severity_scale"] == [1, 5]


def test_understand_text_creates_a_record(client) -> None:
    response = client.post("/api/v1/understand/text", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["request_id"] == "REQ-1001"
    assert body["category"] == "WATER"
    assert body["sub_category"] == "WATER_SUPPLY_DISRUPTION"
    assert 1 <= body["severity"] <= 5
    assert body["severity_band"] in {e.value for e in SeverityBand}
    assert body["stage_status"]


def test_duplicate_submission_conflicts_instead_of_duplicating(client) -> None:
    """Re-submitting an already-understood request must not create a second row.

    A 409 plus the dedicated ``/understand/rerun/{request_id}`` endpoint keeps
    accidental double-processing visible rather than silently overwriting.
    """
    first = client.post("/api/v1/understand/text", json=_payload())
    assert first.status_code == 201

    second = client.post("/api/v1/understand/text", json=_payload())
    assert second.status_code == 409
    assert "already" in second.json()["detail"].lower()

    listing = client.get("/api/v1/understand")
    assert listing.status_code == 200
    rows = [row["request_id"] for row in listing.json()["items"]]
    assert rows.count("REQ-1001") == 1


def test_rerun_is_the_explicit_way_to_reprocess(client) -> None:
    client.post("/api/v1/understand/text", json=_payload())
    response = client.post("/api/v1/understand/rerun/REQ-1001")
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "REQ-1001"
    assert body["category"] == "WATER"

    listing = client.get("/api/v1/understand")
    assert len(listing.json()["items"]) == 1


def test_blank_text_is_rejected(client) -> None:
    response = client.post("/api/v1/understand/text", json={"request_id": "REQ-BLANK", "text": "   "})
    assert response.status_code in {400, 422}


def test_missing_text_is_rejected(client) -> None:
    response = client.post("/api/v1/understand/text", json={"request_id": "REQ-NOTEXT"})
    assert response.status_code in {400, 422}


def test_short_request_id_is_rejected(client) -> None:
    response = client.post("/api/v1/understand/text", json=_payload(request_id="x"))
    assert response.status_code in {400, 422}


def test_tamil_request_is_classified(client) -> None:
    response = client.post(
        "/api/v1/understand/text",
        json=_payload(request_id="REQ-2001", text="கழிவு நீர் சாலையில் பாய்ந்து கொண்டிருக்கு"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["detected_language"] == "ta"
    assert body["category"] == "SANITATION"


def test_batch_processes_each_item_independently(client) -> None:
    response = client.post(
        "/api/v1/understand/batch",
        json={
            "requests": [
                {"request_id": "REQ-B1", "text": "No water supply in our colony"},
                {"request_id": "REQ-B2", "text": "Transformer is burning and sparking"},
                {"request_id": "REQ-B3", "text": "Deep pothole caused an accident"},
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["total"] == 3
    assert len(body["results"]) == 3
    assert body["failed"] == 0
    categories = {row["category"] for row in body["results"]}
    assert categories == {"WATER", "ELECTRICITY", "ROAD"}


def test_batch_reports_failures_without_aborting(client) -> None:
    response = client.post(
        "/api/v1/understand/batch",
        json={
            "requests": [
                {"request_id": "REQ-OK", "text": "No water supply in our colony"},
                {"request_id": "REQ-BAD", "text": "  "},
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["total"] == 2
    assert len(body["results"]) == 1
    assert body["failed"] == 1


def test_audio_upload_degrades_gracefully_without_asr(client) -> None:
    """No ASR backend must still return a usable record, not a 500."""
    response = client.post(
        "/api/v1/understand/audio",
        params={"request_id": "REQ-AUDIO-1"},
        files={"audio": ("note.wav", io.BytesIO(b"RIFF0000WAVEfmt "), "audio/wav")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["stage_status"]["asr"] in {"SKIPPED", "FAILED", "OK"}
    assert body["warnings"]


def test_image_upload_is_analysed(client) -> None:
    response = client.post(
        "/api/v1/understand/image",
        params={"request_id": "REQ-IMG-1"},
        files={"image": ("p.jpg", io.BytesIO(b"\xff\xd8\xff\xe0not-really-a-jpeg"), "image/jpeg")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["stage_status"]["image_analysis"] in {"SKIPPED", "FAILED", "OK"}


def test_get_single_record(client) -> None:
    client.post("/api/v1/understand/text", json=_payload())
    response = client.get("/api/v1/understand/REQ-1001")
    assert response.status_code == 200
    assert response.json()["request_id"] == "REQ-1001"


def test_get_missing_record_returns_404(client) -> None:
    assert client.get("/api/v1/understand/REQ-NOPE").status_code == 404


def test_list_supports_pagination(client) -> None:
    for index in range(3):
        client.post("/api/v1/understand/text", json=_payload(request_id=f"REQ-P{index}"))
    response = client.get("/api/v1/understand", params={"limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) <= 2
    assert "total" in body


def test_rerun_missing_record_returns_404(client) -> None:
    assert client.post("/api/v1/understand/rerun/REQ-NOPE").status_code == 404


def test_category_breakdown_sums_to_record_count(client) -> None:
    client.post("/api/v1/understand/text", json=_payload(request_id="REQ-C1"))
    client.post(
        "/api/v1/understand/text",
        json=_payload(request_id="REQ-C2", text="Transformer is burning and sparking"),
    )
    response = client.get("/api/v1/analytics/categories")
    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert sum(row["count"] for row in rows) == 2
    assert {row["category"] for row in rows} == {"WATER", "ELECTRICITY"}


def test_language_breakdown(client) -> None:
    client.post("/api/v1/understand/text", json=_payload(request_id="REQ-L1"))
    client.post(
        "/api/v1/understand/text",
        json=_payload(request_id="REQ-L2", text="கழிவு நீர் சாலையில் பாய்ந்து கொண்டிருக்கு"),
    )
    response = client.get("/api/v1/analytics/languages")
    assert response.status_code == 200
    rows = {row["language"]: row["count"] for row in response.json()}
    assert rows["en"] == 1
    assert rows["ta"] == 1