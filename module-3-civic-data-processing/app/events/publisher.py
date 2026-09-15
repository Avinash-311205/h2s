"""Event publishing (Module 3 -> Module 4).

Architecture
------------
``OutboxEventPublisher`` is the only publisher the pipeline knows about. It

1. writes the event to the ``event_outbox`` table in the *same transaction* as
   the processed record, then
2. hands the serialized event to an :class:`EventTransport`.

Because the durable write happens first, a Redis outage can never lose an
event: the row stays ``PENDING``/``FAILED`` and ``retry_pending`` delivers it
later. Swapping in Kafka or RabbitMQ is a matter of writing another transport.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import DataQualityStatus, EventStatus
from app.core.logging import get_logger
from app.core.utils import new_id, utcnow
from app.models.civic_record import ProcessedCivicRecord
from app.repositories.event_repository import EventOutboxRepository

logger = get_logger(__name__)

EVENT_RECORD_PROCESSED = "CIVIC_RECORD_PROCESSED"
EVENT_RECORD_NEEDS_REVIEW = "CIVIC_RECORD_NEEDS_REVIEW"
EVENT_RECORD_REJECTED = "CIVIC_RECORD_REJECTED"

SCHEMA_VERSION = "1.0"


@dataclass
class CivicEvent:
    event_type: str
    channel: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: new_id("EVT"))
    request_id: Optional[str] = None
    issue_group_id: Optional[str] = None

    def serialize(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, default=str)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "channel": self.channel,
            "request_id": self.request_id,
            "issue_group_id": self.issue_group_id,
            "payload": self.payload,
        }


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------
class EventTransport(ABC):
    """Low-level delivery mechanism."""

    name: str = "abstract"

    @abstractmethod
    def publish(self, channel: str, message: str) -> None:
        """Deliver ``message`` to ``channel``. Raises on failure."""
        raise NotImplementedError


class RedisEventTransport(EventTransport):
    name = "redis"

    def __init__(self, url: Optional[str] = None):
        self.url = url or settings.redis_url
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=settings.geocoding_timeout_seconds,
                socket_timeout=settings.geocoding_timeout_seconds,
            )
        return self._client

    def publish(self, channel: str, message: str) -> None:
        import redis

        try:
            self.client.publish(channel, message)
        except redis.exceptions.RedisError as exc:  # pragma: no cover - requires a live Redis
            raise RuntimeError(f"redis publish failed: {exc}") from exc


class NoopEventTransport(EventTransport):
    """Development/test transport that logs instead of publishing."""

    name = "noop"

    def publish(self, channel: str, message: str) -> None:
        logger.info("event_not_published_noop_transport", extra={"channel": channel, "bytes": len(message)})


def get_transport() -> EventTransport:
    key = (settings.event_publisher or "noop").strip().casefold()
    if key == "redis":
        return RedisEventTransport()
    if key == "noop":
        return NoopEventTransport()
    logger.warning("unknown_event_publisher", extra={"publisher": settings.event_publisher})
    return NoopEventTransport()


# ---------------------------------------------------------------------------
# publisher
# ---------------------------------------------------------------------------
class EventPublisher(ABC):
    @abstractmethod
    def publish(self, event: CivicEvent) -> bool:
        raise NotImplementedError


class OutboxEventPublisher(EventPublisher):
    """Durable, retryable publisher backed by the ``event_outbox`` table."""

    def __init__(self, repository: EventOutboxRepository, transport: Optional[EventTransport] = None):
        self.repository = repository
        self.transport = transport or get_transport()

    # ------------------------------------------------------------------
    def enqueue(self, event: CivicEvent) -> CivicEvent:
        """Persist the event as ``PENDING`` (no commit - caller owns the tx)."""
        self.repository.add(
            event_id=event.event_id,
            event_type=event.event_type,
            channel=event.channel,
            payload=event.payload,
            request_id=event.request_id,
            issue_group_id=event.issue_group_id,
        )
        return event

    def dispatch(self, event_id: str, commit: bool = True) -> bool:
        """Attempt delivery for a previously enqueued event. Never raises."""
        record = self.repository.get(event_id)
        if record is None:
            logger.warning("event_not_found", extra={"event_id": event_id})
            return False
        if record.status == EventStatus.PUBLISHED.value:
            return True

        message = json.dumps(record.payload, ensure_ascii=False, default=str)
        try:
            self.transport.publish(record.channel, message)
        except Exception as exc:
            self.repository.mark_failed(event_id, str(exc))
            if commit:
                self.repository.db.commit()
            logger.warning(
                "event_publish_failed",
                extra={"event_id": event_id, "event_type": record.event_type, "error": str(exc)},
            )
            return False

        self.repository.mark_published(event_id)
        if commit:
            self.repository.db.commit()
        logger.info(
            "event_published",
            extra={"event_id": event_id, "event_type": record.event_type, "channel": record.channel},
        )
        return True

    def publish(self, event: CivicEvent) -> bool:
        """Enqueue and immediately attempt delivery (committing both steps)."""
        self.enqueue(event)
        self.repository.db.commit()
        return self.dispatch(event.event_id)

    # ------------------------------------------------------------------
    def retry_pending(self, limit: Optional[int] = None) -> dict[str, Any]:
        """Re-attempt every ``PENDING``/``FAILED`` event. Returns a summary."""
        batch = limit or settings.event_outbox_batch_size
        pending = self.repository.list_pending(batch)

        published = 0
        failed = 0
        for event in pending:
            if self.dispatch(event.event_id, commit=False):
                published += 1
            else:
                failed += 1
        self.repository.db.commit()

        summary = {"attempted": len(pending), "published": published, "failed": failed}
        if pending:
            logger.info("event_outbox_retry", extra=summary)
        return summary


def publisher_for_session(db: Session, transport: Optional[EventTransport] = None) -> OutboxEventPublisher:
    return OutboxEventPublisher(EventOutboxRepository(db), transport)


# ---------------------------------------------------------------------------
# payload construction
# ---------------------------------------------------------------------------
def event_type_for_status(data_quality_status: str) -> str:
    if data_quality_status == DataQualityStatus.VALID.value:
        return EVENT_RECORD_PROCESSED
    if data_quality_status == DataQualityStatus.NEEDS_REVIEW.value:
        return EVENT_RECORD_NEEDS_REVIEW
    return EVENT_RECORD_REJECTED


def build_event_for_record(
    record: ProcessedCivicRecord,
    channel: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> CivicEvent:
    """Build the downstream event for a processed civic record."""
    event_type = event_type_for_status(record.data_quality_status)

    payload: dict[str, Any] = {
        "event": event_type,
        "schema_version": SCHEMA_VERSION,
        "request_id": record.request_id,
        "issue_group_id": record.issue_group_id,
        "category": record.category,
        "sub_category": record.sub_category,
        "severity": record.severity,
        "language": record.language,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "village": record.village,
        "ward": record.ward,
        "taluk": record.taluk,
        "district": record.district,
        "state": record.state,
        "country": record.country,
        "location_status": record.location_status,
        "is_duplicate": record.is_duplicate,
        "report_count": record.issue_group.report_count if record.issue_group else 1,
        "data_quality_score": record.data_quality_score,
        "data_quality_status": record.data_quality_status,
        "processing_status": record.processing_status,
        "processed_at": (record.processed_at or occurred_at or utcnow()).isoformat(),
    }

    return CivicEvent(
        event_type=event_type,
        channel=channel or settings.event_channel,
        payload=payload,
        request_id=record.request_id,
        issue_group_id=record.issue_group_id,
    )
