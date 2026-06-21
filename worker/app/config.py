"""Environment-driven settings and structured logging for the worker service.

Two responsibilities, both pure infrastructure (no business logic):

1. ``Settings`` — a ``pydantic-settings`` model that reads the configuration
   variables from REFERENCE §10 (``REDIS_URL``, ``RQ_QUEUE_NAME``, ``NUM_SHOTS``,
   ``JOB_TIMEOUT``, ``RESULT_TTL``, ``MAX_RETRIES``, ``LOG_LEVEL``) from the
   environment, falling back to the documented defaults.
2. ``configure_logging`` / ``get_task_logger`` — a structured (JSON) logging
   setup that writes to stdout and stamps every line with a ``task_id``
   correlation field, so one task can be traced end-to-end (REFERENCE §8).

This module is a deliberate mirror of ``api/app/config.py`` rather than a shared
package, to keep each image's build context independent (see the note at the top
of IMPLEMENTATION_PLAN.md). Keep the two copies in sync.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, sourced from the environment.

    Field names map to upper-cased environment variables (case-insensitive),
    so ``redis_url`` is populated from ``REDIS_URL`` and so on. Defaults match
    REFERENCE §10 exactly.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    redis_url: str = "redis://redis:6379/0"
    rq_queue_name: str = "default"
    num_shots: int = 1024
    job_timeout: int = 180
    result_ttl: int = 86400
    max_retries: int = 2
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide singleton ``Settings`` instance."""
    return Settings()


# --------------------------------------------------------------------------- #
# Structured logging
# --------------------------------------------------------------------------- #


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line JSON for stdout (12-factor).

    The optional ``task_id`` attribute (attached via ``extra=`` or a
    ``LoggerAdapter``) is promoted to a top-level field so logs can be filtered
    and correlated by task across the API and worker containers.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        task_id = getattr(record, "task_id", None)
        if task_id is not None:
            payload["task_id"] = task_id

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Install the JSON formatter on the root logger, writing to stdout.

    Idempotent: existing handlers are cleared so repeated calls (e.g. app
    startup + worker bootstrap) don't double-emit lines. ``level`` defaults to
    the configured ``LOG_LEVEL``.
    """
    resolved_level = (level or get_settings().log_level).upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)


def get_task_logger(
    task_id: str, name: str = "app"
) -> logging.LoggerAdapter:
    """Return a logger that stamps every line with ``task_id``."""
    return logging.LoggerAdapter(logging.getLogger(name), {"task_id": task_id})
