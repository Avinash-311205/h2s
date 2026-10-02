from __future__ import annotations

from datetime import datetime, timezone

from fastapi import UploadFile

from app.core.config import settings
from app.core.utils import build_point_wkt, sanitize_filename, utcnow
from app.models.request import CitizenRequest
from app.queue.redis_client import RedisUnavailable, publish_request_received
from app.repositories.request_repository import RequestRepository
from app.storage.minio_client import StorageUnavailable, upload_file


class RequestService:
    def __init__(self, repository: RequestRepository):
        self.repository = repository

    def create_request(self, payload: dict) -> CitizenRequest:
        request_id = self._generate_request_id()
        location = payload.get("location")
        latitude = payload.get("latitude")
        longitude = payload.get("longitude")
        request = CitizenRequest(
            request_id=request_id,
            channel=payload["channel"],
            user_id=payload.get("user_id"),
            text=(payload.get("text") or "").strip() or None,
            latitude=latitude,
            longitude=longitude,
            geom=build_point_wkt(longitude, latitude),
            location=location.strip() if isinstance(location, str) and location.strip() else None,
            created_at=utcnow(),
            status="RECEIVED",
        )
        self.repository.create(request)

        # Announce to Module 2. If the event bus is down the request is kept but
        # marked PUBLISH_FAILED with the reason, so the submission is visible and
        # retryable instead of silently disappearing into the pipeline.
        try:
            envelope = publish_request_received(
                request_id=request_id,
                status=request.status,
                payload={
                    "channel": request.channel,
                    "user_id": request.user_id,
                    "text": request.text,
                    "latitude": request.latitude,
                    "longitude": request.longitude,
                    "location": request.location,
                    "created_at": request.created_at.isoformat() if request.created_at else None,
                },
            )
        except RedisUnavailable as exc:
            request = self.repository.update_publish_state(
                request_id, "PUBLISH_FAILED", pipeline_error=str(exc)[:500]
            )
        else:
            request = self.repository.update_publish_state(
                request_id,
                "PUBLISHED",
                event_id=envelope["event_id"],
                published_at=utcnow(),
            )
        return request

    def upload_media(self, request_id: str, file: UploadFile, media_type: str) -> CitizenRequest:
        record = self.repository.get_by_id(request_id)
        if record is None:
            raise ValueError("Request not found")

        allowed = {
            "image": {"image/jpeg", "image/png", "image/webp"},
            "audio": {"audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4"},
        }
        content_type = (file.content_type or "").lower()
        if media_type not in allowed or content_type not in allowed[media_type]:
            raise ValueError(f"Unsupported media type for {media_type}.")

        max_bytes = settings.max_file_size_mb * 1024 * 1024
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
        if size > max_bytes:
            raise ValueError(f"File exceeds the {settings.max_file_size_mb}MB limit.")

        filename = sanitize_filename(file.filename or "upload")
        object_key = f"citizen-requests/{request_id}/{media_type}s/{filename}"
        try:
            file_url = upload_file(
                settings.minio_bucket_name, object_key, file.file, content_type or "application/octet-stream"
            )
        except StorageUnavailable as exc:
            # Never substitute a placeholder URL for a failed upload.
            raise ValueError(str(exc)) from exc

        if media_type == "image":
            record.image_url = file_url
        else:
            record.audio_url = file_url

        self.repository.update_media(request_id, image_url=record.image_url, audio_url=record.audio_url)
        return record

    def get_request(self, request_id: str) -> CitizenRequest:
        record = self.repository.get_by_id(request_id)
        if record is None:
            raise ValueError("Request not found")
        return record

    def _generate_request_id(self) -> str:
        year = datetime.now(timezone.utc).year
        count = self.repository.count() + 1
        return f"REQ-{year}-{count:06d}"
