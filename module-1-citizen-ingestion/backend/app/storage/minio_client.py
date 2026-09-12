from __future__ import annotations

from typing import BinaryIO

from minio import Minio

from app.core.config import settings


try:
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )
except Exception:  # pragma: no cover - fallback for local/offline tests
    client = None


def ensure_bucket(bucket_name: str) -> None:
    if client is None:
        return
    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
    except Exception:
        return


def upload_file(bucket_name: str, object_key: str, file_obj: BinaryIO, content_type: str) -> str:
    if client is None:
        return f"https://example-storage.local/{bucket_name}/{object_key}"

    try:
        ensure_bucket(bucket_name)
        file_obj.seek(0)
        client.put_object(
            bucket_name,
            object_key,
            file_obj,
            length=-1,
            part_size=10 * 1024 * 1024,
            content_type=content_type,
        )
    except Exception:
        return f"https://example-storage.local/{bucket_name}/{object_key}"

    return f"http://{settings.minio_endpoint}/{bucket_name}/{object_key}"
