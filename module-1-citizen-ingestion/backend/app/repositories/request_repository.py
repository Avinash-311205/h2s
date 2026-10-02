from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.request import CitizenRequest


class RequestRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, request: CitizenRequest) -> CitizenRequest:
        self.db.add(request)
        self.db.commit()
        self.db.refresh(request)
        return request

    def count(self) -> int:
        return self.db.query(CitizenRequest).count()

    def get_by_id(self, request_id: str) -> Optional[CitizenRequest]:
        return self.db.query(CitizenRequest).filter(CitizenRequest.request_id == request_id).first()

    def update_media(self, request_id: str, image_url: Optional[str] = None, audio_url: Optional[str] = None) -> CitizenRequest:
        record = self.get_by_id(request_id)
        if record is None:
            raise ValueError("Request not found")
        if image_url is not None:
            record.image_url = image_url
        if audio_url is not None:
            record.audio_url = audio_url
        self.db.commit()
        self.db.refresh(record)
        return record

    def update_status(self, request_id: str, status: str) -> CitizenRequest:
        record = self.get_by_id(request_id)
        if record is None:
            raise ValueError("Request not found")
        record.status = status
        self.db.commit()
        self.db.refresh(record)
        return record

    def update_publish_state(
        self,
        request_id: str,
        pipeline_status: str,
        event_id: Optional[str] = None,
        published_at: Optional[datetime] = None,
        pipeline_error: Optional[str] = None,
    ) -> CitizenRequest:
        """Record the outcome of announcing this request to Module 2."""
        record = self.get_by_id(request_id)
        if record is None:
            raise ValueError("Request not found")
        record.pipeline_status = pipeline_status
        if event_id is not None:
            record.event_id = event_id
        if published_at is not None:
            record.published_at = published_at
        record.pipeline_error = pipeline_error
        self.db.commit()
        self.db.refresh(record)
        return record
