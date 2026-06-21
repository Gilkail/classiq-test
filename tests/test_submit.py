"""POST /tasks validation & acceptance path (REFERENCE §6.1, failure F1).

These assertions are unit-level on the API edge: they prove that valid bodies
are accepted with ``202`` + a UUID, and that every malformed-body variant is
rejected with ``400`` *before* anything is enqueued.
"""

from __future__ import annotations

import uuid

import httpx


def _assert_uuid(value: str) -> None:
    """Fail unless ``value`` is a canonical UUID string."""
    # uuid.UUID(str(...)) round-trip rejects anything that isn't a real UUID.
    assert str(uuid.UUID(value)) == value


def test_valid_submit_returns_202_and_uuid(
    client: httpx.Client, bell_state_qasm: str
) -> None:
    resp = client.post("/tasks", json={"qc": bell_state_qasm})

    assert resp.status_code == 202
    body = resp.json()
    assert body["message"] == "Task submitted successfully."
    _assert_uuid(body["task_id"])


def test_missing_qc_returns_400(client: httpx.Client) -> None:
    resp = client.post("/tasks", json={})

    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["message"]  # non-empty validation detail


def test_empty_qc_returns_400(client: httpx.Client) -> None:
    resp = client.post("/tasks", json={"qc": ""})

    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_wrong_qc_type_returns_400(client: httpx.Client) -> None:
    # qc must be a string; a non-string violates the schema at the edge.
    resp = client.post("/tasks", json={"qc": 123})

    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_extra_key_returns_400(
    client: httpx.Client, bell_state_qasm: str
) -> None:
    # extra="forbid" on SubmitRequest rejects unknown keys.
    resp = client.post(
        "/tasks", json={"qc": bell_state_qasm, "unexpected": "field"}
    )

    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_malformed_json_returns_400(client: httpx.Client) -> None:
    # A body that isn't valid JSON at all is still surfaced as a 400 (F1).
    resp = client.post(
        "/tasks",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 400
    assert resp.json()["status"] == "error"
