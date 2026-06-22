"""Shared fixtures & helpers for the integration suite (Phase 5).

These tests exercise the *running* stack over HTTP (REFERENCE §2 testing layer),
so there are no in-process imports of the API or worker — only an ``httpx``
client pointed at the published port. Configuration comes from the environment
so the same suite runs against a local ``docker compose up`` or any other host.

Environment overrides:
    BASE_URL   API base URL                 (default ``http://localhost:8000``)
    NUM_SHOTS  expected total shots per job  (default ``1024``; must match the
               stack's ``NUM_SHOTS`` so the lifecycle total assertion holds)
    POLL_TIMEOUT  seconds to wait for a terminal state (default ``60``)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
NUM_SHOTS = int(os.environ.get("NUM_SHOTS", "1024"))
POLL_TIMEOUT = float(os.environ.get("POLL_TIMEOUT", "60"))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "0.5"))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    """Read a QASM fixture file as text."""
    return (FIXTURES_DIR / name).read_text()


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    """Session-scoped HTTP client bound to the API base URL."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _require_live_api(client: httpx.Client) -> None:
    """Skip the whole suite (loudly) if the stack isn't reachable.

    Integration tests are meaningless without the running services; a connection
    error here means ``docker compose up`` wasn't started, not a code defect.
    """
    try:
        resp = client.get("/health")
    except httpx.HTTPError as exc:  # connection refused, DNS, timeout, …
        pytest.skip(f"API not reachable at {BASE_URL} ({exc}); is the stack up?")
    if resp.status_code != 200:
        pytest.skip(f"API /health returned {resp.status_code}; stack not ready.")


@pytest.fixture
def bell_state_qasm() -> str:
    """Valid OpenQASM 3 Bell-state circuit (REFERENCE Appendix A)."""
    return _load_fixture("bell_state.qasm")


@pytest.fixture
def ghz_state_qasm() -> str:
    """Valid 3-qubit GHZ circuit (H + two CNOTs, then measure all)."""
    return _load_fixture("ghz_state.qasm")


@pytest.fixture
def hadamard_pair_qasm() -> str:
    """Valid 2-qubit circuit: H on both, entangling CNOT, then measure."""
    return _load_fixture("hadamard_pair.qasm")


@pytest.fixture
def invalid_qasm() -> str:
    """Malformed OpenQASM 3 payload that must fail to parse."""
    return _load_fixture("invalid.qasm")


def poll_until_terminal(
    client: httpx.Client,
    task_id: str,
    timeout: float = POLL_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> dict:
    """GET ``/tasks/{id}`` until status != ``pending`` or ``timeout`` elapses.

    Returns the final JSON body. Raises ``AssertionError`` if the task is still
    pending after ``timeout`` (a hang is a test failure, not an infinite wait).
    """
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/tasks/{task_id}")
        assert resp.status_code == 200, (
            f"unexpected status polling {task_id}: "
            f"{resp.status_code} {resp.text}"
        )
        body = resp.json()
        if body.get("status") != "pending":
            return body
        time.sleep(interval)

    raise AssertionError(
        f"task {task_id} still pending after {timeout}s (last body: {body})"
    )
