"""Task state accessor for the API service (``task:{id}`` HASH, REFERENCE §5.1).

All Redis access for task state lives here so route handlers stay thin. The
connection is non-decoding (see ``redis_client``); hash fields are decoded
explicitly below.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import get_settings
from .redis_client import get_redis

KEY_PREFIX = "task:"

# Constant body text mapped on read (REFERENCE §6.2).
PENDING_MESSAGE = "Task is still in progress."


def _key(task_id: str) -> str:
    return f"{KEY_PREFIX}{task_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_pending(task_id: str) -> None:
    """Write the initial ``pending`` hash + TTL before the job is enqueued."""
    settings = get_settings()
    key = _key(task_id)
    now = _now()
    # Pipeline so the hash and its TTL are set in one round-trip.
    pipe = get_redis().pipeline()
    pipe.hset(
        key,
        mapping={
            "status": "pending",
            "shots": settings.num_shots,  # config echo (REFERENCE §5.1)
            "created_at": now,
            "updated_at": now,
        },
    )
    pipe.expire(key, settings.result_ttl)
    pipe.execute()


def get(task_id: str) -> dict | None:
    """Return the response-shaped body for ``task_id``, or ``None`` if absent."""
    raw = get_redis().hgetall(_key(task_id))
    if not raw:
        return None

    # Decode bytes -> str (connection does not auto-decode).
    fields = {k.decode(): v.decode() for k, v in raw.items()}
    status = fields.get("status")

    if status == "completed":
        return {"status": "completed", "result": json.loads(fields["result"])}
    if status == "error":
        return {"status": "error", "message": fields.get("message", "")}
    # Treat anything else as still pending.
    return {"status": "pending", "message": PENDING_MESSAGE}


def rollback(task_id: str) -> None:
    """Delete the pending hash when the subsequent enqueue fails."""
    get_redis().delete(_key(task_id))
