"""Failure-mode coverage (REFERENCE §8: F2, F5).

Confirms that handled failures converge to a *terminal* state rather than
hanging, and that unknown ids are reported as not-found.
"""

from __future__ import annotations

import httpx

from conftest import poll_until_terminal


def test_invalid_qasm_reaches_terminal_error(
    client: httpx.Client, invalid_qasm: str
) -> None:
    # Malformed QASM3 parses fine as a JSON string (so it's accepted, 202), but
    # the worker fails to parse it and writes a terminal error (F2).
    submit = client.post("/tasks", json={"qc": invalid_qasm})
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]

    body = poll_until_terminal(client, task_id)

    assert body["status"] == "error", f"expected terminal error, got: {body}"
    assert body["message"] == "Invalid QASM3 syntax"


def test_unknown_id_returns_404(client: httpx.Client) -> None:
    # A well-formed but never-submitted UUID has no task:{id} hash (F5).
    resp = client.get("/tasks/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["message"] == "Task not found."
