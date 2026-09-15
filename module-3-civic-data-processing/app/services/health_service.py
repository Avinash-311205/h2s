"""Health and readiness checks."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.utils import utcnow
from app.repositories.event_repository import EventOutboxRepository
from app.services.normalization_service import get_dictionary

logger = get_logger(__name__)


class HealthService:
    """Best-effort liveness probe for the dependencies Module 3 relies on."""

    def __init__(self, db: Session):
        self.db = db

    def check(self) -> dict[str, Any]:
        checks: dict[str, Any] = {
            "database": self._database(),
            "redis": self._redis(),
            "geocoding_provider": self._geocoding(),
            "event_publisher": self._event_publisher(),
            "normalization_dictionary": self._dictionary(),
            "event_outbox": self._outbox(),
        }

        # Only the database is a hard dependency for the API to be useful.
        healthy = checks["database"]["status"] == "ok"
        return {
            "status": "ok" if healthy else "degraded",
            "service": settings.service_name,
            "version": settings.version,
            "environment": settings.environment,
            "timestamp": utcnow(),
            "checks": checks,
        }

    # ------------------------------------------------------------------
    def _database(self) -> dict[str, Any]:
        try:
            self.db.execute(text("SELECT 1"))
            return {"status": "ok", "dialect": self.db.bind.dialect.name if self.db.bind else "unknown"}
        except Exception as exc:
            logger.error("health_database_failed", extra={"error": str(exc)})
            return {"status": "error", "detail": str(exc)}

    def _redis(self) -> dict[str, Any]:
        if (settings.event_publisher or "").casefold() != "redis":
            return {"status": "disabled", "detail": "EVENT_PUBLISHER is not redis"}
        try:
            from app.events.publisher import RedisEventTransport

            client = RedisEventTransport(settings.redis_url).client
            client.ping()
            return {"status": "ok", "url_scheme": settings.redis_url.split("://", 1)[0]}
        except Exception as exc:
            logger.warning("health_redis_failed", extra={"error": str(exc)})
            return {"status": "error", "detail": str(exc)}

    @staticmethod
    def _event_publisher() -> dict[str, Any]:
        configured = (settings.event_publisher or "").casefold()
        if configured == "redis":
            status = "ok"
        elif configured == "noop":
            status = "disabled"
        else:
            status = "error"
        return {
            "status": status,
            "configured": settings.event_publisher,
            "channel": settings.event_channel,
        }

    @staticmethod
    def _geocoding() -> dict[str, Any]:
        try:
            from app.services.geocoding_service import get_geocoder

            return {
                "status": "ok",
                "configured": settings.geocoding_provider,
                "active": get_geocoder().name,
                "fallback": settings.geocoding_fallback_provider,
            }
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    @staticmethod
    def _dictionary() -> dict[str, Any]:
        try:
            dictionary = get_dictionary()
            return {"status": "ok", **dictionary.summary()}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    def _outbox(self) -> dict[str, Any]:
        try:
            stats = EventOutboxRepository(self.db).stats()
            return {"status": "ok", "by_status": stats}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}
