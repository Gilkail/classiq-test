"""Terminal task-state writer for the worker (``task:{id}`` HASH, REFERENCE §5.1).

Mirrors the field names and key namespace used by the API's ``task_store`` so the
state model stays consistent across services. Each write stamps ``updated_at``
and refreshes the TTL so terminal results self-evict on the same clock as
pending tasks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import get_settings
from .redis_client import get_redis

KEY_PREFIX = "task:"


def _key(task_id: str) -> str:
    return f"{KEY_PREFIX}{task_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(task_id: str, fields: dict[str, object]) -> None:
    """HSET the given fields + ``updated_at`` and refresh the TTL atomically."""
    key = _key(task_id)
    fields["updated_at"] = _now()
    # Pipeline so the hash update and TTL refresh share one round-trip.
    pipe = get_redis().pipeline()
    pipe.hset(key, mapping=fields)
    pipe.expire(key, get_settings().result_ttl)
    pipe.execute()


def set_completed(task_id: str, result: dict[str, int]) -> None:
    """Write the terminal ``completed`` state with JSON-encoded counts."""
    _write(task_id, {"status": "completed", "result": json.dumps(result)})


def set_error(task_id: str, kind: str, message: str) -> None:
    """Write the terminal ``error`` state with a machine-readable ``error_kind``."""
    _write(task_id, {"status": "error", "error_kind": kind, "message": message})
