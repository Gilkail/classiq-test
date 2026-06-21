"""Worker service package — RQ execution service (Qiskit/Aer)."""

from . import jobs  # noqa: F401  — required so RQ can resolve app.jobs.run_task
