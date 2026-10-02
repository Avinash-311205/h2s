"""HTTP client for Module 1 (Citizen Ingestion).

Module 1's announcement is enough to *start* work, but the stored row is the
authoritative record, so the consumer re-reads it over HTTP before running the
understanding pipeline. That keeps Module 1 the single source of truth for
citizen input and means a re-published event cannot invent content.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class IngestionUnavailable(RuntimeError):
    """Module 1 could not be reached or returned something unusable."""


class IngestionClient:
    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None):
        self.base_url = (base_url or settings.ingestion_base_url).rstrip("/")
        self.timeout = timeout or settings.ingestion_fetch_timeout_seconds

    def fetch_request(self, request_id: str) -> dict[str, Any]:
        """Fetch the stored citizen request.

        Raises:
            IngestionUnavailable: on transport failure, non-2xx, or a body that
                is not a JSON object. Never returns partially-valid data.
        """
        url = f"{self.base_url}/api/v1/requests/{request_id}"
        try:
            response = httpx.get(url, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise IngestionUnavailable(f"could not fetch {request_id} from Module 1: {exc}") from exc

        if response.status_code == 404:
            raise IngestionUnavailable(
                f"Module 1 has no request {request_id}; the event arrived before the row was readable"
            )
        if response.status_code >= 400:
            raise IngestionUnavailable(
                f"Module 1 returned {response.status_code} for {request_id}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise IngestionUnavailable(f"Module 1 returned non-JSON for {request_id}") from exc

        if not isinstance(payload, dict):
            raise IngestionUnavailable(f"Module 1 returned an unexpected body for {request_id}")

        # Trust the stored row's identity over the event's.
        payload["request_id"] = request_id
        return payload

    def healthy(self) -> bool:
        """Whether Module 1's health endpoint answers."""
        try:
            response = httpx.get(f"{self.base_url}/api/v1/health", timeout=self.timeout)
        except httpx.HTTPError:
            return False
        return response.status_code < 500
