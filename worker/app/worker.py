"""RQ worker entrypoint (REFERENCE §4.2).

Bootstraps structured logging and runs a blocking RQ worker against the
configured queue. Invoked as ``python -m app.worker`` from the worker container;
``.work()`` blocks, popping jobs and dispatching them to ``jobs.run_task``.
"""

from __future__ import annotations

import logging
import os

from rq import Queue, SimpleWorker, Worker

from .config import configure_logging, get_settings
from .redis_client import get_redis


def main() -> None:
    """Configure logging and start the blocking RQ worker loop."""
    configure_logging()
    settings = get_settings()
    connection = get_redis()

    queue = Queue(settings.rq_queue_name, connection=connection)
    logging.getLogger("app.worker").info(
        "worker starting on queue '%s'", settings.rq_queue_name
    )

    # SimpleWorker runs jobs in-process so debugpy breakpoints hit (RQ 2.10+).
    worker_cls = SimpleWorker if os.environ.get("DEBUGPY_ENABLE") == "1" else Worker
    worker_cls([queue], connection=connection).work()


if __name__ == "__main__":
    main()
