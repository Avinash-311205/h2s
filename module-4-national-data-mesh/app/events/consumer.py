"""Redis consumer: Module 3 -> Module 4.

Module 3 publishes ``CIVIC_RECORD_PROCESSED`` on ``settings.civic_channel``. Each
message is applied through ``CivicRecordIngestService``, the same code the
``POST /ingest/civic-records`` route calls, so a replayed or manually-submitted
event takes an identical path.

Redis pub/sub has no delivery guarantee and no redelivery, so a crash between
ack and commit can drop an event. This is stated rather than papered over: the
fix is a durable inbox or a broker with acknowledgements, and until that exists
``/lineage/{request_id}`` plus the event_id in the Module 3 outbox are what an
operator uses to detect and replay a gap.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.database.connection import SessionLocal
from app.services.ingest_service import CivicRecordIngestService

logger = get_logger(__name__)


class CivicRecordConsumer:
    """Subscribes to Module 3's output and folds records into the mesh."""

    def __init__(self, channel: Optional[str] = None):
        self.channel = channel or settings.civic_channel
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.ingested = 0
        self.duplicates = 0
        self.rejected = 0
        self.last_error: Optional[str] = None
        self.connected = False

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="civic-record-consumer", daemon=True
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
            "ingested": self.ingested,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "last_error": self.last_error,
        }

    # -- thread body ----------------------------------------------------
    def _run(self) -> None:
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
                    if message is None or message.get("type") != "message":
                        continue
                    try:
                        self.handle(message.get("data") or "")
                    except Exception as exc:  # never kill the loop on one bad event
                        self.rejected += 1
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
        """Ingest one ``CIVIC_RECORD_PROCESSED`` envelope."""
        try:
            envelope = json.loads(raw)
        except ValueError:
            logger.warning("consumer_bad_json")
            return None
        if not isinstance(envelope, dict):
            logger.warning("consumer_bad_envelope")
            return None

        db = SessionLocal()
        try:
            result = CivicRecordIngestService(db).ingest(envelope)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        if result.accepted:
            if result.duplicate:
                self.duplicates += 1
            else:
                self.ingested += 1
        else:
            self.rejected += 1
            logger.warning("consumer_event_rejected", extra={"reason": result.reason})

        logger.info(
            "civic_record_consumed",
            extra={
                "request_id": result.request_id,
                "duplicate": result.duplicate,
                "ward_code": result.ward_code,
                "counted_in_demand": result.counted_in_demand,
            },
        )
        return result.request_id


_consumer: Optional[CivicRecordConsumer] = None


def get_consumer() -> CivicRecordConsumer:
    global _consumer
    if _consumer is None:
        _consumer = CivicRecordConsumer()
    return _consumer


def start_consumer() -> CivicRecordConsumer:
    consumer = get_consumer()
    consumer.start()
    return consumer


def stop_consumer() -> None:
    global _consumer
    if _consumer is not None:
        _consumer.stop()
