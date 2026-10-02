"""Redis consumer: Module 1 -> Module 2.

Runs as a daemon thread started by the FastAPI lifespan. For every
``REQUEST_RECEIVED`` announcement on ``settings.ingestion_channel`` it

1. fetches the authoritative stored row from Module 1 over HTTP,
2. runs the existing offline understanding pipeline on it,
3. stores the result keyed by ``request_id`` (already UNIQUE in the schema),
4. publishes ``UNDERSTANDING_COMPLETED`` through the durable outbox.

Idempotency: ``understanding_records.request_id`` is UNIQUE and
``understanding_events.request_id`` is UNIQUE, so a redelivered event can never
create a second record or a second downstream event.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.database.connection import SessionLocal
from app.events.publisher import (
    OutboxEventPublisher,
    build_completion_event,
    get_transport,
    new_event_id,
)
from app.ingestion.client import IngestionClient, IngestionUnavailable
from app.repositories.understanding_event_repository import UnderstandingEventRepository
from app.repositories.understanding_repository import UnderstandingRepository
from app.services import understanding_service

logger = get_logger(__name__)


class UnderstandingConsumer:
    """Background subscriber for Module 1 announcements."""

    def __init__(
        self,
        channel: Optional[str] = None,
        client: Optional[IngestionClient] = None,
    ) -> None:
        self.channel = channel or settings.ingestion_channel
        self.client = client or IngestionClient()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.processed = 0
        self.skipped = 0
        self.failed = 0
        self.last_error: Optional[str] = None
        self.connected = False

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="understanding-consumer", daemon=True
        )
        self._thread.start()
        logger.info("consumer_started", extra={"channel": self.channel})

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("consumer_stopped", extra={"channel": self.channel})

    def status(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "connected": self.connected,
            "processed": self.processed,
            "skipped": self.skipped,
            "failed": self.failed,
            "last_error": self.last_error,
        }

    # -- thread body ----------------------------------------------------
    def _run(self) -> None:
        # Reconnect rather than die: a Redis restart during a demo must not
        # permanently stop the pipeline.
        while not self._stop.is_set():
            try:
                import redis

                client = redis.Redis.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=settings.redis_socket_timeout_seconds,
                )
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(self.channel)
                self.connected = True
                logger.info("consumer_subscribed", extra={"channel": self.channel})

                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if message is None:
                        continue
                    if message.get("type") != "message":
                        continue
                    try:
                        self.handle(message.get("data") or "")
                    except Exception as exc:  # a bad event must not kill the loop
                        self.failed += 1
                        self.last_error = f"{type(exc).__name__}: {exc}"
                        logger.exception("consumer_event_failed")

                try:
                    pubsub.close()
                except Exception:
                    pass
            except Exception as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("consumer_connect_failed", extra={"error": str(exc)})
                self._stop.wait(2.0)
            finally:
                self.connected = False

    # -- event handling -------------------------------------------------
    def handle(self, raw: str) -> Optional[str]:
        """Process one announcement. Returns the request_id handled, if any."""
        try:
            envelope = json.loads(raw)
        except ValueError:
            logger.warning("consumer_bad_json")
            return None
        if not isinstance(envelope, dict):
            logger.warning("consumer_bad_envelope")
            return None

        event_type = envelope.get("event") or envelope.get("event_type")
        if event_type != settings.ingestion_event_type:
            logger.debug("consumer_ignored_event", extra={"event_type": event_type})
            return None

        request_id = envelope.get("correlation_id") or envelope.get("request_id")
        if not request_id:
            logger.warning("consumer_missing_correlation_id")
            return None

        source_event_id = envelope.get("event_id")
        source_channel = envelope.get("channel")

        db = SessionLocal()
        try:
            return self._process(
                db,
                request_id=request_id,
                source_event_id=source_event_id,
                source_channel=source_channel,
                envelope=envelope,
            )
        finally:
            db.close()

    def _process(
        self,
        db,
        *,
        request_id: str,
        source_event_id: Optional[str],
        source_channel: Optional[str],
        envelope: dict[str, Any],
    ) -> Optional[str]:
        event_repo = UnderstandingEventRepository(db)

        # Already handled: a redelivered announcement is a no-op, not an error.
        existing_event = event_repo.get_by_request_id(request_id)
        if existing_event is not None and existing_event.status == "PUBLISHED":
            self.skipped += 1
            logger.info("consumer_duplicate_ignored", extra={"request_id": request_id})
            return request_id

        record_repo = UnderstandingRepository(db)
        if record_repo.get_by_request_id(request_id) is not None:
            self.skipped += 1
            logger.info("consumer_already_understood", extra={"request_id": request_id})
            return request_id

        # The event tells us to start; Module 1's stored row is the truth.
        try:
            citizen_request = self.client.fetch_request(request_id)
        except IngestionUnavailable as exc:
            self.failed += 1
            self.last_error = str(exc)
            logger.warning("consumer_fetch_failed", extra={"request_id": request_id, "error": str(exc)})
            return None

        text = citizen_request.get("text")
        image_url = citizen_request.get("image_url")
        audio_url = citizen_request.get("audio_url")
        if not text and not image_url and not audio_url:
            # Nothing understandable (e.g. location-only pin). Recorded as a
            # skip so it is visible rather than silently vanishing.
            self.skipped += 1
            logger.info("consumer_no_understandable_content", extra={"request_id": request_id})
            return None

        started = time.perf_counter()
        result = understanding_service.understand(
            request_id=request_id,
            text=text,
            audio_bytes=None,
            image_bytes=None,
            hint_language=None,
            hint_latitude=citizen_request.get("latitude"),
            hint_longitude=citizen_request.get("longitude"),
        )
        processing_ms = int((time.perf_counter() - started) * 1000)

        source_channel_value = (citizen_request.get("channel") or "UNKNOWN")[:20].upper()
        record = record_repo.save(
            result,
            source_channel=source_channel_value,
            processing_ms=processing_ms,
        )

        payload = build_completion_event(
            request_id,
            record=record,
            source_event_id=source_event_id,
            source_channel=source_channel,
        )
        event_id = new_event_id()

        # Same transaction as the record insert: if Redis is down the event row
        # is still durable and gets retried.
        publisher = OutboxEventPublisher(event_repo, get_transport())
        publisher.enqueue(
            event_id=event_id,
            request_id=request_id,
            payload=payload,
            source_event_id=source_event_id,
            source_channel=source_channel,
        )
        db.commit()
        published = publisher.dispatch(event_id)

        self.processed += 1
        if not published:
            self.last_error = "completion event queued but not yet published"
        logger.info(
            "consumer_processed",
            extra={
                "request_id": request_id,
                "category": record.category,
                "sub_category": record.sub_category,
                "severity": record.severity,
                "event_id": event_id,
                "published": published,
            },
        )
        return request_id


_consumer: Optional[UnderstandingConsumer] = None


def get_consumer() -> UnderstandingConsumer:
    global _consumer
    if _consumer is None:
        _consumer = UnderstandingConsumer()
    return _consumer


def start_consumer() -> UnderstandingConsumer:
    consumer = get_consumer()
    consumer.start()
    return consumer


def stop_consumer() -> None:
    global _consumer
    if _consumer is not None:
        _consumer.stop()
