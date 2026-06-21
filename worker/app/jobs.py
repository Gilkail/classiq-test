"""RQ job function — the enqueued callable (REFERENCE §4.2, §7.4).

``run_task`` is referenced by dotted path from the API and resolved here in the
worker image. It converts every handled failure into a terminal ``error`` state
and deliberately does **not** re-raise, guaranteeing a polling client always
converges to a terminal answer (terminal-state guarantee).
"""

from __future__ import annotations

from rq import get_current_job

from . import task_store
from .config import get_settings, get_task_logger
from .execution import InvalidQasmError, run_circuit


def run_task(qc: str) -> None:
    """Execute the circuit for the current RQ job and write its terminal state."""
    # job_id == task_id (set by the API at enqueue), so it threads through logs.
    task_id = get_current_job().id
    log = get_task_logger(task_id, "app.worker")

    log.info("task started")
    try:
        result = run_circuit(qc, get_settings().num_shots)
    except InvalidQasmError as exc:
        # Expected bad input (F2): handled, not re-raised.
        log.warning("invalid QASM3: %s", exc)
        task_store.set_error(task_id, "invalid_qasm3", "Invalid QASM3 syntax")
        return
    except Exception as exc:  # ExecutionError + any other fault (F3)
        # Unexpected simulation/runtime fault: log traceback, still terminal.
        log.error("execution failed: %s", exc, exc_info=True)
        task_store.set_error(task_id, "execution_error", str(exc))
        return

    task_store.set_completed(task_id, result)
    log.info("task completed")
