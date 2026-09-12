import json
from datetime import datetime, timezone

import redis

from app.core.config import settings

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def publish_request_received(request_id: str, status: str) -> None:
    try:
        payload = {
            "event": "REQUEST_RECEIVED",
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status,
        }
        redis_client.publish("citizen-requests", json.dumps(payload))
    except redis.exceptions.RedisError:
        return
