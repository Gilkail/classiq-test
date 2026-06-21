"""FastAPI application — public HTTP contract (REFERENCE §6).

Route handlers are thin: validation is done by Pydantic, all Redis access goes
through ``task_store``, and the worker job is enqueued by dotted reference so the
heavy Qiskit stack never needs importing here.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from rq import Retry

from . import task_store
from .config import configure_logging, get_settings, get_task_logger
from .redis_client import get_queue, get_redis
from .schemas import SubmitRequest, SubmitResponse

# Dotted path RQ serializes; resolved by the worker image, not the API.
JOB_FUNC = "app.jobs.run_task"

configure_logging()
logger = logging.getLogger("app.api")

app = FastAPI(title="Quantum Circuit Execution Engine")


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError):
    """Shape Pydantic/JSON errors into the documented 400 body (F1)."""
    detail = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
    return JSONResponse(status_code=400, content={"status": "error", "message": detail})


@app.post("/tasks", status_code=202)
async def submit_task(req: SubmitRequest) -> SubmitResponse:
    """Validate, persist ``pending``, then enqueue the execution job."""
    task_id = str(uuid4())
    settings = get_settings()
    log = get_task_logger(task_id, "app.api")

    try:
        # Write pending BEFORE enqueue so an immediate GET sees it (REFERENCE §4.1).
        task_store.create_pending(task_id)
        get_queue().enqueue(
            JOB_FUNC,
            req.qc,
            job_id=task_id,  # job_id == task_id -> one ID end-to-end, idempotent.
            job_timeout=settings.job_timeout,
            result_ttl=settings.result_ttl,
            retry=Retry(max=settings.max_retries),
        )
    except RedisError:
        # Roll back the orphaned hash and fail fast (F6).
        task_store.rollback(task_id)
        log.exception("enqueue failed; rolled back pending task")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Task queue unavailable, retry later."},
        )

    log.info("task submitted")
    return SubmitResponse(task_id=task_id)


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Return terminal/pending state, or 404 when the id is unknown (F5)."""
    state = task_store.get(task_id)
    if state is None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Task not found."},
        )
    return state


@app.get("/health")
async def health():
    """Liveness + Redis reachability probe (F7)."""
    try:
        get_redis().ping()
    except RedisError:
        return JSONResponse(status_code=503, content={"status": "error", "redis": "down"})
    return {"status": "ok"}
