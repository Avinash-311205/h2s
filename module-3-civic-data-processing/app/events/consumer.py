"""Redis consumer: Module 2 -> Module 3.

Module 2 publishes ``UNDERSTANDING_COMPLETED`` on
``settings.understanding_channel``. For each one this consumer calls this
service's **own** ``POST /api/v1/civic/process`` over HTTP.

Going through the real endpoint (rather than calling
``CivicProcessingService.process`` inline) is deliberate: the endpoint is the
contract Module 4 and any operator already use, so the consumer exercises the
same validation, normalization, geocoding, de-duplication, outbox write and
event publication as an ordinary caller. There is no second code path that
could drift from the API.

Idempotency is owned by the endpoint: ``process`` is keyed on ``request_id``
and returns ``idempotent: true`` for a replay, so a redelivered event cannot
create a duplicate civic record.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class UpstreamUnavailable(RuntimeError):
    """Module 2 could not be reached, or sent something unusable."""


class UnderstandingConsumer:
    """Background subscriber that feeds Module 2 output into /civic/process."""

    def __init__(self, channel: Optional[str] = None, base_url: Optional[str] = None):
        self.channel = channel or settings.understanding_channel
        self.base_url = (base_url or settings.self_base_url).rstrip("/")
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.processed = 0
        self.idempotent = 0
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
            "target_endpoint": f"{self.base_url}/api/v1/civic/process",
            "processed": self.processed,
            "idempotent": self.idempotent,
            "failed": self.failed,
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
                    socket_connect_timeout=settings.geocoding_timeout_seconds,
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
                    except Exception as exc:  # one bad event must not kill the loop
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
        """Process one ``UNDERSTANDING_COMPLETED`` event."""
        try:
            envelope = json.loads(raw)
        except ValueError:
            logger.warning("consumer_bad_json")
            return None
        if not isinstance(envelope, dict):
            logger.warning("consumer_bad_envelope")
            return None

        event_type = envelope.get("event") or envelope.get("event_type")
        if event_type != settings.understanding_event_type:
            logger.debug("consumer_ignored_event", extra={"event_type": event_type})
            return None

        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict):
            logger.warning("consumer_bad_payload")
            return None

        request_id = (
            envelope.get("correlation_id")
            or envelope.get("request_id")
            or payload.get("request_id")
        )
        if not request_id:
            logger.warning("consumer_missing_correlation_id")
            return None

        # Module2RecordIn accepts these names; keep request_id authoritative.
        body = dict(payload)
        body["request_id"] = request_id
        body.setdefault("source_module", "module-2-ai-understanding")

        return self._process(body, request_id, envelope.get("event_id"))

    def _process(self, body: dict[str, Any], request_id: str, source_event_id: Optional[str]) -> Optional[str]:
        import httpx

        url = f"{self.base_url}/api/v1/civic/process"
        try:
            response = httpx.post(
                url,
                json=body,
                timeout=settings.upstream_process_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            self.failed += 1
            self.last_error = f"could not reach {url}: {exc}"
            logger.warning(
                "consumer_process_call_failed",
                extra={"request_id": request_id, "error": str(exc)},
            )
            return None

        if response.status_code >= 500:
            self.failed += 1
            self.last_error = f"{url} returned {response.status_code}"
            logger.warning(
                "consumer_process_error",
                extra={"request_id": request_id, "status": response.status_code},
            )
            return None

        if response.status_code >= 400:
            self.failed += 1
            self.last_error = f"{url} rejected the payload: {response.text[:300]}"
            logger.warning(
                "consumer_process_rejected",
                extra={"request_id": request_id, "status": response.status_code},
            )
            return None

        try:
            result = response.json()
        except ValueError:
            self.failed += 1
            self.last_error = f"{url} returned non-JSON"
            return None

        if result.get("idempotent"):
            self.idempotent += 1
        else:
            self.processed += 1

        logger.info(
            "consumer_processed",
            extra={
                "request_id": request_id,
                "source_event_id": source_event_id,
                "idempotent": bool(result.get("idempotent")),
                "event_type": result.get("event_type"),
                "event_published": bool(result.get("event_published")),
                "data_quality_status": (result.get("record") or {}).get("data_quality_status"),
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
