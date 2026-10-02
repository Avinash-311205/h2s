"""Object storage access for citizen media (images, voice notes).

Media is an *optional* part of Module 1: the civic pipeline in the demo path is
text and location only. So object storage must never block startup or a text
submission.

Every operation is bounded by an explicit timeout and reports unavailability
instead of inventing a result. An earlier version returned
``https://example-storage.local/...`` when MinIO was down, which handed callers
a URL that pointed nowhere and looked like a successful upload.
"""

from __future__ import annotations

import logging
from typing import BinaryIO, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Reasons reported by :func:`storage_status`, surfaced through /health.
_STORAGE_ERROR: Optional[str] = None


def _build_client():
    if not settings.minio_enabled:
        return None
    try:
        import urllib3
        from minio import Minio
    except ImportError:  # pragma: no cover - dependency is declared
        logger.warning("minio_not_installed")
        return None
    try:
        # minio.Minio() has no timeout argument, so the bound has to come from
        # the HTTP pool it uses. Without this, an absent object store blocks the
        # request until the OS TCP timeout instead of failing in ~2s.
        http_client = urllib3.PoolManager(
            timeout=urllib3.util.Timeout(
                connect=settings.minio_timeout_seconds,
                read=settings.minio_timeout_seconds,
            ),
            retries=False,
        )
        return Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
            http_client=http_client,
        )
    except Exception as exc:  # pragma: no cover - misconfiguration
        logger.warning("minio_client_unavailable: %s", exc)
        return None


client = _build_client()


class StorageUnavailable(RuntimeError):
    """Raised when a media upload is requested but object storage is down."""


def storage_status() -> dict[str, object]:
    """Report object-storage availability for /health."""
    if not settings.minio_enabled or client is None:
        return {"available": False, "reason": "disabled", "endpoint": settings.minio_endpoint}
    try:
        client.bucket_exists(settings.minio_bucket_name)
        return {"available": True, "reason": None, "endpoint": settings.minio_endpoint}
    except Exception as exc:
        return {"available": False, "reason": type(exc).__name__, "endpoint": settings.minio_endpoint}


def ensure_bucket(bucket_name: str) -> bool:
    """Create the bucket if needed. Returns False when storage is unavailable.

    Never raises: startup must continue without object storage so the text
    pipeline still works. The caller reports the state through /health.
    """
    global _STORAGE_ERROR
    if client is None:
        _STORAGE_ERROR = "disabled"
        return False
    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
        _STORAGE_ERROR = None
        return True
    except Exception as exc:
        _STORAGE_ERROR = type(exc).__name__
        logger.warning("minio_unavailable: %s", exc)
        return False


def upload_file(bucket_name: str, object_key: str, file_obj: BinaryIO, content_type: str) -> str:
    """Upload media and return its URL.

    Raises :class:`StorageUnavailable` rather than returning a placeholder URL,
    so a caller can never mistake a failed upload for a stored one.
    """
    if client is None:
        raise StorageUnavailable(
            "object storage is not configured; media upload is unavailable"
        )
    try:
        ensure_bucket(bucket_name)
        file_obj.seek(0)
        result = client.put_object(
            bucket_name,
            object_key,
            file_obj,
            length=-1,
            part_size=10 * 1024 * 1024,
            content_type=content_type,
        )
    except StorageUnavailable:
        raise
    except Exception as exc:
        raise StorageUnavailable(f"media upload failed: {exc}") from exc

    endpoint = settings.minio_endpoint
    scheme = "https" if getattr(client, "secure", False) else "http"
    return f"{scheme}://{endpoint}/{bucket_name}/{result.object_name}"