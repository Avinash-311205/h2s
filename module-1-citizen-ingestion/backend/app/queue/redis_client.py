"""Redis event publishing for Module 1 -> Module 2.

Module 1 is the origin of the civic pipeline: it announces every accepted
submission on a Redis channel and Module 2 picks it up. That makes Redis part
of the intended architecture, not an optional extra.

Two rules follow from that:

- A publish failure is never swallowed. A citizen request that is stored but
  never announced would sit in Module 1 forever with no way for the pipeline to
  notice, so the caller is told and the request is marked accordingly.
- Redis being unavailable is reported explicitly at startup and in ``/health``
  rather than being hidden behind a successful-looking request.

Event shape (``REQUEST_RECEIVED``)::

    {
      "event": "REQUEST_RECEIVED",
      "event_id": "EVT-...",         # unique per publication
      "correlation_id": "REQ-...",   # == request_id, minted by Module 1
      "source_module": "module-1-citizen-ingestion",
      "schema_version": "1.0",
      "timestamp": "...Z",
      "status": "RECEIVED",
      "payload": {...}               # the fields Module 2 needs
    }

``correlation_id`` equals the ``request_id`` so one identifier follows the
submission through Modules 2, 3 and 4.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import redis

from app.core.config import settings

SCHEMA_VERSION = "1.0"
SOURCE_MODULE = "module-1-citizen-ingestion"
EVENT_REQUEST_RECEIVED = "REQUEST_RECEIVED"


class RedisUnavailable(RuntimeError):
    """Raised when Redis cannot be reached or a publish fails."""


def new_event_id() -> str:
    return f"EVT-{uuid.uuid4().hex[:16]}"


def make_client() -> "redis.Redis":
    """Build a client with an explicit socket timeout.

    Without this, an unreachable Redis blocks the request until the OS TCP
    timeout, which looks like a hung service rather than a dependency failure.
    """
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )


redis_client = make_client()


def ping() -> bool:
    """Whether Redis is reachable right now. Used by /health and startup."""
    try:
        return bool(redis_client.ping())
    except redis.exceptions.RedisError:
        return False


def publish_request_received(
    request_id: str,
    status: str,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Announce an accepted submission.

    Returns the published envelope (including its ``event_id``) so the caller can
    record it against the request. Raises :class:`RedisUnavailable` on failure.
    """
    event_id = new_event_id()
    envelope: dict[str, Any] = {
        "event": EVENT_REQUEST_RECEIVED,
        "event_id": event_id,
        "correlation_id": request_id,
        "request_id": request_id,
        "source_module": SOURCE_MODULE,
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "payload": payload or {},
    }
    try:
        redis_client.publish(settings.event_channel, json.dumps(envelope, ensure_ascii=False))
    except redis.exceptions.RedisError as exc:
        raise RedisUnavailable(
            f"could not publish {event_id} to '{settings.event_channel}': {exc}"
        ) from exc
    return envelope