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


def test_upload_media_to_request():
    create_response = client.post(
        "/api/v1/requests",
        json={"text": "Drainage problem near school.", "channel": "web"},
    )
    request_id = create_response.json()["request_id"]

    image_bytes = b"fake-image-bytes"
    response = client.post(
        f"/api/v1/requests/{request_id}/media",
        files={"file": ("sample.png", image_bytes, "image/png")},
    )

    assert response.status_code == 200
    assert "image_url" in response.json()
