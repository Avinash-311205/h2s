from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CitizenRequestCreate(BaseModel):
    channel: str = Field(..., min_length=1)
    user_id: Optional[str] = None
    text: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location: Optional[str] = None

    @model_validator(mode="after")
    def validate_content(self):
        text = (self.text or "").strip()
        if not text and self.latitude is None and self.longitude is None and not self.location:
            raise ValueError("Request cannot be empty: provide text, location, or both.")
        if self.latitude is not None and not (-90 <= self.latitude <= 90):
            raise ValueError("Latitude must be between -90 and 90.")
        if self.longitude is not None and not (-180 <= self.longitude <= 180):
            raise ValueError("Longitude must be between -180 and 180.")
        return self


class CitizenRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: str
    status: str
    channel: str
    user_id: Optional[str] = None
    text: Optional[str] = None
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location: Optional[str] = None
    created_at: datetime


class MediaUploadResponse(BaseModel):
    request_id: str
    status: str
    image_url: Optional[str] = None
    audio_url: Optional[str] = None
