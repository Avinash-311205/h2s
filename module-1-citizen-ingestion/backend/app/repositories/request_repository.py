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
