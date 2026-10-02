"""Event publishing (Module 2 -> Module 3).

The completion event is written to ``understanding_events`` in the same
transaction as the understanding record, then handed to Redis. A Redis outage
therefore cannot lose an event: the row stays ``PENDING``/``FAILED`` and
``retry_pending`` delivers it on the next startup or manual retry.

Publication failures are recorded, never swallowed.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.understanding_event_repository import UnderstandingEventRepository

logger = get_logger(__name__)

SCHEMA_VERSION = "1.0"
SOURCE_MODULE = "module-2-ai-understanding"


def new_event_id() -> str:
    return f"EVT-{uuid.uuid4().hex[:16]}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventTransport(ABC):
    name: str = "abstract"

    @abstractmethod
    def publish(self, channel: str, message: str) -> None:
        """Deliver ``message``. Raises on failure."""
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
                socket_connect_timeout=settings.redis_socket_timeout_seconds,
                socket_timeout=settings.redis_socket_timeout_seconds,
            )
        return self._client

    def publish(self, channel: str, message: str) -> None:
        import redis

        try:
            self.client.publish(channel, message)
        except redis.exceptions.RedisError as exc:
            raise RuntimeError(f"redis publish failed: {exc}") from exc


class NoopEventTransport(EventTransport):
    """Test transport: records the message instead of publishing it."""

    name = "noop"

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> None:
        self.messages.append((channel, message))


def get_transport() -> EventTransport:
    key = (getattr(settings, "event_publisher", "redis") or "redis").strip().casefold()
    if key == "noop":
        return NoopEventTransport()
    return RedisEventTransport()


def build_completion_event(
    request_id: str,
    *,
    record,
    source_event_id: Optional[str],
    source_channel: Optional[str],
) -> dict[str, Any]:
    """Build the ``UNDERSTANDING_COMPLETED`` payload from a stored record.

    Field names match Module 3's ``Module2RecordIn`` exactly, so Module 3 can
    hand this straight to its existing normalizer without relabelling.
    """
    payload: dict[str, Any] = {
        "event": settings.understanding_event_type,
        "schema_version": SCHEMA_VERSION,
        "source_module": SOURCE_MODULE,
        "correlation_id": request_id,
        "request_id": request_id,
        "timestamp": utcnow_iso(),

        "language": record.detected_language,
        "category": record.category,
        "sub_category": record.sub_category,
        "text": record.english_text or record.original_text,
        "severity": record.severity,

        "latitude": record.hint_latitude,
        "longitude": record.hint_longitude,
        "location_text": record.original_text,

        "channel": record.source_channel,
        "status": record.status,
        "category_confidence": record.category_confidence,
        "severity_band": record.severity_band,
        "severity_confidence": record.severity_confidence,
        "matched_keywords": record.matched_keywords or [],
        "entities": record.entities or [],
        "translation_provider": record.translation_provider,
        "translation_is_gloss": bool(record.translation_is_gloss),
        "stage_status": record.stage_status or {},
        "warnings": record.warnings or [],
        "processing_ms": record.processing_ms,
    }
    return payload


class OutboxEventPublisher:
    """Durable, retryable publisher backed by ``understanding_events``."""

    def __init__(
        self,
        repository: UnderstandingEventRepository,
        transport: Optional[EventTransport] = None,
    ):
        self.repository = repository
        self.transport = transport or get_transport()

    def enqueue(
        self,
        *,
        event_id: str,
        request_id: str,
        payload: dict[str, Any],
        source_event_id: Optional[str] = None,
        source_channel: Optional[str] = None,
        commit: bool = False,
    ) -> str:
        """Persist the event as ``PENDING``. The caller owns the transaction."""
        self.repository.add(
            event_id=event_id,
            request_id=request_id,
            event_type=settings.understanding_event_type,
            channel=settings.understanding_channel,
            payload=payload,
            source_event_id=source_event_id,
            source_channel=source_channel,
        )
        if commit:
            self.repository.session.commit()
        return event_id

    def dispatch(self, event_id: str, commit: bool = True) -> bool:
        """Attempt delivery for an enqueued event. Never raises."""
        record = self.repository.get(event_id)
        if record is None:
            logger.warning("event_not_found", extra={"event_id": event_id})
            return False
        if record.status == "PUBLISHED":
            return True

        envelope = {
            "event_id": record.event_id,
            "event": record.event_type,
            "event_type": record.event_type,
            "channel": record.channel,
            "schema_version": SCHEMA_VERSION,
            "source_module": SOURCE_MODULE,
            "correlation_id": record.request_id,
            "request_id": record.request_id,
            "source_event_id": record.source_event_id,
            "timestamp": utcnow_iso(),
            "payload": record.payload,
        }
        message = json.dumps(envelope, ensure_ascii=False, default=str)

        try:
            self.transport.publish(record.channel, message)
        except Exception as exc:
            self.repository.mark_failed(record.event_id, str(exc))
            if commit:
                self.repository.session.commit()
            logger.warning(
                "event_publish_failed",
                extra={
                    "event_id": record.event_id,
                    "request_id": record.request_id,
                    "channel": record.channel,
                    "error": str(exc),
                },
            )
            return False

        self.repository.mark_published(record.event_id)
        if commit:
            self.repository.session.commit()
        logger.info(
            "event_published",
            extra={
                "event_id": record.event_id,
                "request_id": record.request_id,
                "channel": record.channel,
            },
        )
        return True

    def retry_pending(self, limit: Optional[int] = None) -> dict[str, Any]:
        """Re-attempt every ``PENDING``/``FAILED`` event."""
        pending = self.repository.list_pending(limit or 100)
        published = failed = 0
        for event in pending:
            if self.dispatch(event.event_id, commit=False):
                published += 1
            else:
                failed += 1
        self.repository.session.commit()
        summary = {"attempted": len(pending), "published": published, "failed": failed}
        if pending:
            logger.info("event_outbox_retry", extra=summary)
        return summary


def publisher_for_session(
    db: Session, transport: Optional[EventTransport] = None
) -> OutboxEventPublisher:
    return OutboxEventPublisher(UnderstandingEventRepository(db), transport)
