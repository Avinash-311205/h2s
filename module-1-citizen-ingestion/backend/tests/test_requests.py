from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_create_request_generates_id_and_status():
    payload = {
        "text": "The road in front of the clinic is damaged and full of potholes.",
        "latitude": 12.9716,
        "longitude": 77.5946,
        "channel": "web",
    }

    response = client.post("/api/v1/requests", json=payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "RECEIVED"
    assert body["request_id"].startswith("REQ-")


def test_rejects_empty_request():
    response = client.post("/api/v1/requests", json={"channel": "web"})

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_validates_coordinates():
    response = client.post(
        "/api/v1/requests",
        json={"text": "Water issue", "latitude": 91, "longitude": 77.5946, "channel": "web"},
    )

    assert response.status_code == 400


def test_get_request_returns_metadata():
    create_response = client.post(
        "/api/v1/requests",
        json={"text": "Need better street lighting.", "latitude": 13.0, "longitude": 77.0, "channel": "web"},
    )
    request_id = create_response.json()["request_id"]

    response = client.get(f"/api/v1/requests/{request_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == request_id
    assert body["status"] == "RECEIVED"


def _create_request(text: str = "Drainage problem near school.") -> str:
    response = client.post("/api/v1/requests", json={"text": text, "channel": "web"})
    return response.json()["request_id"]


def test_upload_media_returns_a_real_object_url_when_storage_is_up(monkeypatch):
    """The stored URL must be the one object storage reports, not a stand-in."""
    request_id = _create_request()

    monkeypatch.setattr(
        "app.services.request_service.upload_file",
        lambda bucket, key, file_obj, content_type: f"http://127.0.0.1:9000/{bucket}/{key}",
    )

    response = client.post(
        f"/api/v1/requests/{request_id}/media",
        files={"file": ("sample.png", b"fake-image-bytes", "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["image_url"].startswith("http://127.0.0.1:9000/")


def test_upload_media_fails_loudly_when_object_storage_is_down(monkeypatch):
    """No object stored means no URL. Returning a placeholder would be a lie."""
    request_id = _create_request()

    def unavailable(*args, **kwargs):
        from app.storage.minio_client import StorageUnavailable

        raise StorageUnavailable("object storage is unavailable")

    monkeypatch.setattr("app.services.request_service.upload_file", unavailable)

    response = client.post(
        f"/api/v1/requests/{request_id}/media",
        files={"file": ("sample.png", b"fake-image-bytes", "image/png")},
    )

    assert response.status_code == 400
    assert "storage" in response.json()["detail"].lower()
    # Nothing was written, so nothing is advertised.
    stored = client.get(f"/api/v1/requests/{request_id}").json()
    assert stored.get("image_url") is None


def test_upload_media_rejects_an_unsupported_type():
    request_id = _create_request()

    response = client.post(
        f"/api/v1/requests/{request_id}/media",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )

    assert response.status_code == 400
