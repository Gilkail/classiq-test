"""Full submit → poll → completed lifecycle (REFERENCE §6, Appendix A).

Drives real circuits end-to-end through the running stack and asserts the
terminal ``completed`` body matches the documented contract: a counts dict whose
keys lie in the expected measurement space and whose values sum to ``NUM_SHOTS``.
"""

from __future__ import annotations

import httpx

from conftest import NUM_SHOTS, poll_until_terminal

# Classical outcome spaces for the fixture circuits.
OUTCOMES_2Q = {"00", "01", "10", "11"}
OUTCOMES_3Q = {f"{a}{b}{c}" for a in "01" for b in "01" for c in "01"}


def _assert_completed_lifecycle(
    client: httpx.Client,
    qasm: str,
    valid_keys: set[str],
    *,
    expected_keys: set[str] | None = None,
) -> dict:
    """Submit ``qasm``, poll to terminal, and assert a valid ``completed`` body."""
    submit = client.post("/tasks", json={"qc": qasm})
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]

    body = poll_until_terminal(client, task_id)

    assert body["status"] == "completed", f"unexpected terminal body: {body}"
    result = body["result"]
    assert isinstance(result, dict) and result, "result must be a non-empty dict"

    assert set(result).issubset(valid_keys), f"unexpected keys: {set(result)}"
    assert all(isinstance(v, int) and v > 0 for v in result.values())
    assert sum(result.values()) == NUM_SHOTS

    if expected_keys is not None:
        assert set(result).issubset(expected_keys), (
            f"expected outcomes within {expected_keys}, got: {set(result)}"
        )

    return result


def test_bell_state_completes_with_valid_counts(
    client: httpx.Client, bell_state_qasm: str
) -> None:
    _assert_completed_lifecycle(
        client,
        bell_state_qasm,
        OUTCOMES_2Q,
        expected_keys={"00", "11"},
    )


def test_ghz_state_completes_with_valid_counts(
    client: httpx.Client, ghz_state_qasm: str
) -> None:
    # Three-qubit GHZ: |000⟩ + |111⟩ — only correlated all-zeros / all-ones.
    _assert_completed_lifecycle(
        client,
        ghz_state_qasm,
        OUTCOMES_3Q,
        expected_keys={"000", "111"},
    )


def test_hadamard_pair_completes_with_valid_counts(
    client: httpx.Client, hadamard_pair_qasm: str
) -> None:
    # H on both qubits then CNOT — full 2-bit outcome space, no single-key bias.
    _assert_completed_lifecycle(
        client,
        hadamard_pair_qasm,
        OUTCOMES_2Q,
    )


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
