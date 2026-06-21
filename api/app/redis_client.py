"""Redis connectivity for the API service.

Provides a process-wide singleton ``redis.Redis`` built from ``REDIS_URL`` and a
helper that returns the RQ ``Queue`` used to enqueue execution jobs. All Redis
access in the API goes through these factories so connection construction lives
in exactly one place.

Note on ``decode_responses``: the connection intentionally uses the default
(bytes responses). RQ requires a non-decoding connection because it stores
pickled job payloads; ``task_store`` (Phase 2) decodes the ``task:{id}`` hash
fields it reads explicitly.
"""

from __future__ import annotations

from functools import lru_cache

import redis
from rq import Queue

from .config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    """Return a singleton Redis client built from ``REDIS_URL``."""
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url)


@lru_cache
def get_queue() -> Queue:
    """Return the singleton RQ ``Queue`` jobs are enqueued onto."""
    settings = get_settings()
    return Queue(settings.rq_queue_name, connection=get_redis())
