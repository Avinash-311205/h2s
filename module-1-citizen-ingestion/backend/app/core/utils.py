from __future__ import annotations

from datetime import datetime, timezone
import re


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_filename(filename: str) -> str:
    if not filename:
        return "upload"
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename.strip().replace("\\", "/").split("/")[-1])
    return safe_name or "upload"


def build_point_wkt(longitude: float | None, latitude: float | None) -> str | None:
    if longitude is None or latitude is None:
        return None
    return f"POINT({longitude} {latitude})"
