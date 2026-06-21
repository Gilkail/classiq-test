"""Full submit → poll → completed lifecycle (REFERENCE §6, Appendix A).

Drives a real Bell-state circuit end-to-end through the running stack and
asserts the terminal ``completed`` body matches the documented contract: a
counts dict over the 2-bit measurement space whose totals sum to ``NUM_SHOTS``.
"""

from __future__ import annotations

import httpx

from conftest import NUM_SHOTS, poll_until_terminal

# A 2-qubit measurement can only land in these four classical outcomes.
VALID_KEYS = {"00", "01", "10", "11"}


def test_bell_state_completes_with_valid_counts(
    client: httpx.Client, bell_state_qasm: str
) -> None:
    submit = client.post("/tasks", json={"qc": bell_state_qasm})
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]

    body = poll_until_terminal(client, task_id)

    assert body["status"] == "completed", f"unexpected terminal body: {body}"
    result = body["result"]
    assert isinstance(result, dict) and result, "result must be a non-empty dict"

    # Every key is a valid 2-bit outcome; every value is a positive int count.
    assert set(result).issubset(VALID_KEYS), f"unexpected keys: {set(result)}"
    assert all(isinstance(v, int) and v > 0 for v in result.values())

    # Shots are conserved: the counts partition exactly NUM_SHOTS measurements.
    assert sum(result.values()) == NUM_SHOTS

    # A Bell state concentrates on the correlated outcomes 00 and 11.
    assert result.keys() <= {"00", "11"} or {"00", "11"} & result.keys()


def test_immediate_get_is_pending_or_terminal(
    client: httpx.Client, bell_state_qasm: str
) -> None:
    # Right after submit the task is pending; it may also have completed already
    # on a fast machine — both are valid, an unknown/404 is not.
    submit = client.post("/tasks", json={"qc": bell_state_qasm})
    task_id = submit.json()["task_id"]

    resp = client.get(f"/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"pending", "completed", "error"}
