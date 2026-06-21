"""Redis connectivity for the worker service.

Provides a process-wide singleton ``redis.Redis`` built from ``REDIS_URL``. It
backs both the RQ worker bootstrap (Phase 3 ``worker.py``) and the terminal-state
writer (Phase 3 ``task_store.py``), so the worker opens exactly one connection.

Note on ``decode_responses``: the connection uses the default (bytes responses).
RQ requires a non-decoding connection because it stores pickled job payloads; the
worker's ``task_store`` decodes any hash fields it reads explicitly.
"""

from __future__ import annotations

from functools import lru_cache

import redis

from .config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    """Return a singleton Redis client built from ``REDIS_URL``."""
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url)
