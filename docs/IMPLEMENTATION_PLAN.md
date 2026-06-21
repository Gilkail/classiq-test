# Implementation Plan — Quantum Circuit Execution Engine

> **Status:** Task breakdown (pre-implementation)
> **Companion document:** [`REFERENCE.md`](./REFERENCE.md) — architecture & contract decisions
> **Decision locked:** async task manager = **RQ (Redis Queue)**

## How to use this plan

The work is split into **8 self-contained phases**. Each phase lists its
objective, the exact files it creates/edits, the implementation notes needed to
build it, and a concrete *acceptance check*. Phases are ordered by dependency,
so each can be commanded independently, e.g.:

> "Implement Phase 2." · "Do Phases 0–1." · "Run the Phase 7 verification."

A phase is **done** only when its acceptance check passes. Phase 7 re-verifies
the whole system end-to-end.

---

## Proposed directory tree

A monorepo with **isolated dependencies per service** — the API image never
compiles the heavy Qiskit/Aer stack.

```
classiq-test/
├── README.md                     # Phase 6 — root documentation deliverable
├── docker-compose.yml            # Phase 4 — orchestrates redis + api + worker
├── .env.example                  # Phase 4 — documented default configuration
├── .gitignore                    # Phase 0
├── .dockerignore                 # Phase 0
├── Makefile                      # Phase 6 (optional) — up/down/test shortcuts
│
├── docs/
│   ├── REFERENCE.md              # architecture & contract (this baseline)
│   └── IMPLEMENTATION_PLAN.md    # this file
│
├── api/                          # ── FastAPI ingress service ──
│   ├── Dockerfile                # Phase 4 — python:3.10-slim, no Qiskit
│   ├── requirements.txt          # Phase 0 — fastapi, uvicorn, redis, rq, pydantic
│   └── app/
│       ├── __init__.py
│       ├── main.py               # Phase 2 — FastAPI app, routes, exception handlers
│       ├── schemas.py            # Phase 2 — Pydantic request/response models
│       ├── config.py             # Phase 1 — env-driven settings
│       ├── redis_client.py       # Phase 1 — Redis connection + RQ queue factory
│       └── task_store.py         # Phase 2 — read/write task:{id} state hash
│
├── worker/                       # ── RQ execution service ──
│   ├── Dockerfile                # Phase 4 — python:3.10-slim + Qiskit/Aer
│   ├── requirements.txt          # Phase 0 — rq, redis, qiskit, qiskit-aer, qiskit-qasm3-import
│   └── app/
│       ├── __init__.py
│       ├── worker.py             # Phase 3 — RQ worker bootstrap / entrypoint
│       ├── config.py             # Phase 1 — shared settings (mirrors api/config)
│       ├── redis_client.py       # Phase 1 — Redis connection
│       ├── task_store.py         # Phase 3 — write completed/error state
│       ├── jobs.py               # Phase 3 — RQ job function (the enqueued callable)
│       └── execution.py          # Phase 3 — Qiskit service: qasm3.loads → run → counts
│
└── tests/                        # ── integration tests ──
    ├── requirements.txt          # Phase 0 — pytest, httpx
    ├── conftest.py               # Phase 5 — base URL, polling helper, fixtures
    ├── fixtures/
    │   ├── bell_state.qasm        # Phase 5 — valid QASM3 sample
    │   └── invalid.qasm           # Phase 5 — malformed QASM3 sample
    ├── test_submit.py            # Phase 5 — POST validation + 202 path
    ├── test_lifecycle.py         # Phase 5 — submit → poll → completed
    └── test_failures.py          # Phase 5 — invalid QASM3, unknown id, bad payload
```

> `task_store.py`, `config.py`, and `redis_client.py` are intentionally small
> and duplicated per service rather than shared via a package, to keep each
> image's build context independent. If a shared lib is preferred later, extract
> a `common/` package — noted as a refactor seam, not a Phase-1 requirement.

---

## Phase 0 — Scaffolding & dependency manifests

**Objective:** create the directory tree, empty module stubs, dependency files,
and repo hygiene files so later phases only fill in logic.

**Creates:**

- Full tree above with empty `__init__.py` and stub modules (docstring + `TODO`).
- `api/requirements.txt`: `fastapi`, `uvicorn[standard]`, `redis`, `rq`, `pydantic`, `pydantic-settings`.
- `worker/requirements.txt`: `rq`, `redis`, `qiskit`, `qiskit-aer`, `qiskit-qasm3-import`.
- `tests/requirements.txt`: `pytest`, `httpx`.
- `.gitignore` (Python, venv, `__pycache__`, `.env`), `.dockerignore`.

**Acceptance check:** `tree` (or `find`) matches the layout; every directory
imports cleanly as a package; no logic yet.

> Exact version pins are added in Phase 4 when images are first built and
> compatibility is confirmed (Qiskit ≥ 1.1 / qiskit-aer 0.17.x / qiskit-qasm3-import ≥ 0.6).

---

## Phase 1 — Shared configuration & Redis connectivity

**Objective:** centralize settings and Redis/RQ connection construction; no
business logic.

**Implements:**

- `api/app/config.py` & `worker/app/config.py` — `pydantic-settings`-based
  `Settings` reading `REDIS_URL`, `RQ_QUEUE_NAME`, `NUM_SHOTS`, `JOB_TIMEOUT`,
  `RESULT_TTL`, `MAX_RETRIES`, `LOG_LEVEL` (defaults per REFERENCE §10).
- `api/app/redis_client.py` — singleton `redis.Redis` from `REDIS_URL`; helper
  returning an RQ `Queue`.
- `worker/app/redis_client.py` — singleton `redis.Redis` for the worker.
- Logging setup helper (structured stdout, `task_id` correlation field).

**Depends on:** Phase 0.

**Acceptance check:** a throwaway script (or `python -c`) constructs `Settings`
and pings Redis successfully against a local `redis` container.

---

## Phase 2 — API service (FastAPI)

**Objective:** implement the public HTTP contract (REFERENCE §6) and the
submission write-path (REFERENCE §4.1).

**Implements:**

- `api/app/schemas.py`
  - `SubmitRequest`: `qc: str` (min length 1), `extra="forbid"` to reject
    unknown keys.
  - `SubmitResponse`, and status response models for completed/pending/error.
- `api/app/task_store.py`
  - `create_pending(task_id)` → `HSET task:{id}` status=pending + timestamps + TTL.
  - `get(task_id)` → `HGETALL`, decode `result` JSON, return mapped dict or `None`.
  - `rollback(task_id)` → delete hash if enqueue fails.
- `api/app/main.py`
  - `POST /tasks`: validate → `task_id = uuid4()` → `create_pending` → `queue.enqueue(run_task, qc, job_id=task_id, job_timeout=…, retry=Retry(…))` → `202`. On enqueue failure: `rollback` + `503`.
  - `GET /tasks/{id}`: `task_store.get`; map to completed/pending/error JSON; `404` when `None`.
  - `GET /health`: liveness + Redis ping.
  - Exception handlers turning Pydantic `RequestValidationError` into the `400`
    body shape from REFERENCE §6.1.
  - Startup logging; Uvicorn entrypoint.

**Implementation notes:**

- The API enqueues by **import path / reference** to the worker's `run_task`;
  the function need not be importable in the API image — RQ serializes the job by
  qualified name and the **worker** resolves it. Confirm the dotted path matches
  `worker/app/jobs.py:run_task`.
- Keep route handlers thin; all Redis access goes through `task_store`.

**Depends on:** Phase 1.

**Acceptance check (unit-level, no worker yet):** `POST /tasks` with valid body
→ `202` + UUID; malformed bodies → `400`; `GET` of an unknown id → `404`; `GET`
of a just-submitted id → `pending`.

---

## Phase 3 — Worker service (RQ + Qiskit)

**Objective:** consume jobs and produce terminal state, with the exhaustive
error handling from REFERENCE §8.

**Implements:**

- `worker/app/execution.py` — pure execution service:
  - `run_circuit(qasm: str, shots: int) -> dict[str, int]`
  - `qc = qiskit.qasm3.loads(qasm)` — wrap in `try/except QASM3ImporterError` →
    raise a typed `InvalidQasmError("Invalid QASM3 syntax")`.
  - `transpiled = transpile(qc, AerSimulator())`; `counts = AerSimulator().run(transpiled, shots=shots).result().get_counts()` — wrap in `try/except Exception` → `ExecutionError`.
  - Returns the counts dict (string keys, int values).
- `worker/app/task_store.py` — `set_completed(id, result)`, `set_error(id, kind, message)` (`HSET` + TTL refresh + `updated_at`).
- `worker/app/jobs.py` — `run_task(qc: str) -> None`, the enqueued callable:
  - resolves `task_id` from RQ job context, logs start,
  - calls `run_circuit`, on success `set_completed`,
  - on `InvalidQasmError` → `set_error(invalid_qasm3, "Invalid QASM3 syntax")` (log WARNING),
  - on `ExecutionError`/any `Exception` → `set_error(execution_error, …)` (log ERROR w/ traceback),
  - **does not re-raise** for these handled modes (terminal-state guarantee, REFERENCE §7.4).
- `worker/app/worker.py` — entrypoint: build Redis connection, start
  `rq.Worker([queue]).work()` with logging configured.

**Depends on:** Phases 1–2 (shares state-model conventions).

**Acceptance check:** with `redis` + `worker` running, enqueue a Bell-state job
→ `task:{id}` becomes `completed` with a 2-key counts dict; enqueue invalid
QASM3 → `error` / `invalid_qasm3`; both within `job_timeout`.

---

## Phase 4 — Containerization & orchestration

**Objective:** one-command, health-gated stack (REFERENCE §9).

**Implements:**

- `api/Dockerfile` — `python:3.10-slim`, install `api/requirements.txt`, run
  `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- `worker/Dockerfile` — `python:3.10-slim`, install `worker/requirements.txt`
  (Qiskit/Aer), run `python -m app.worker`.
- `docker-compose.yml`:
  - `redis`: `redis:7-alpine`, command enables AOF (`--appendonly yes`),
    named volume, `healthcheck: redis-cli ping`.
  - `api`: build `./api`, `ports: 8000:8000`, env from `.env`,
    `depends_on: redis: condition: service_healthy`.
  - `worker`: build `./worker`, env from `.env`, same `depends_on` gate.
- `.env.example` documenting every variable from REFERENCE §10; pin exact
  package versions now and rebuild.

**Depends on:** Phases 2–3.

**Acceptance check:** `docker compose up --build` starts all three services;
`api` and `worker` only start after `redis` is healthy; `curl
localhost:8000/health` → ok.

---

## Phase 5 — Integration tests (pytest + httpx)

**Objective:** prove the full client lifecycle against the running stack
(REFERENCE §6, §8).

**Implements:**

- `tests/conftest.py` — `BASE_URL` (default `http://localhost:8000`), an `httpx`
  client fixture, and a `poll_until_terminal(task_id, timeout)` helper that GETs
  until status ≠ `pending`.
- `tests/fixtures/bell_state.qasm`, `tests/fixtures/invalid.qasm`.
- `test_submit.py` — valid POST → `202` + UUID; missing `qc` → `400`; empty
  `qc` → `400`; extra key → `400`.
- `test_lifecycle.py` — submit Bell state → poll → `completed`; assert counts
  keys ⊆ {`00`,`01`,`10`,`11`} and shot totals sum to `NUM_SHOTS`.
- `test_failures.py` — invalid QASM3 → terminal `error` with `"Invalid QASM3
  syntax"`; unknown id → `404` `"Task not found."`.

**Depends on:** Phase 4 (tests hit the exposed port, per REFERENCE §2 testing
layer).

**Acceptance check:** `pytest` is green against `docker compose up`; lifecycle
test reaches `completed`; failure tests reach `error`/`404` without hanging.

---

## Phase 6 — Root README & documentation finalization

**Objective:** deliver the assignment's README (architecture, setup, contract,
resiliency, usage examples).

**Implements:** `README.md` at repo root with:

1. Architecture overview (decoupled components + async data flow), distilled
   from REFERENCE §3–§4 with the ASCII diagram.
2. Setup & local execution — `docker compose up --build` as the single command;
   prerequisites; ports.
3. API contract — `POST /tasks` and `GET /tasks/{id}` I/O (REFERENCE §6).
4. Resiliency & failure modes — missing task, invalid QASM3, simulator faults,
   startup race (REFERENCE §7–§8).
5. Usage examples — copy-paste `curl` for submit + poll, and how to run tests.
6. (Optional) `Makefile` targets `up` / `down` / `test` / `logs`.

**Depends on:** Phases 2–5 (document the real, built behavior).

**Acceptance check:** a fresh reader can clone, run one command, submit a
circuit via the README's `curl`, and retrieve a result.

---

## Phase 7 — End-to-end verification & acceptance

**Objective:** confirm the whole deliverable against the assignment checklist.

**Runbook:**

1. `docker compose down -v && docker compose up --build` from a clean state.
2. Submit the Appendix-A Bell state via `curl`; capture `task_id`.
3. Poll `GET /tasks/{id}` → observe `pending` → `completed` with ~50/50 counts.
4. Submit malformed JSON and an invalid QASM3 string → confirm `400` and
   terminal `error` respectively.
5. `GET /tasks/<random-uuid>` → `404`.
6. `docker compose up --scale worker=2` → confirm two workers drain the queue.
7. Kill a worker mid-job (`docker kill`) → confirm the task does not hang in
   `pending` (lands in `error`/retry, REFERENCE §7.2).
8. Run `pytest`; confirm green.

**Verification aids:** generate a diff/file inventory and re-read the
traceability matrix (REFERENCE Appendix B). For higher assurance, a fresh agent
or reviewer repeats steps 1–8 on a clean machine.

**Acceptance check:** every row of the §"Definition of Done" table below is
checked.

---

## Definition of Done (assignment checklist mapping)

| Deliverable (assignment §5) | Phase | Done when |
|---|---|---|
| API server implementation | 2 | `POST`/`GET`/`health` behave per contract |
| Async task-processing components | 3 | worker drains queue → terminal state |
| Dockerfiles for all components | 4 | `api/` + `worker/` images build |
| `docker-compose.yml` orchestrating api + worker + redis | 4 | `up --build` starts all, health-gated |
| README (setup, design, usage) | 6 | one-command run reproducible from README |
| Integration tests (submit/process/retrieve) | 5 | `pytest` green end-to-end |
| Runs via a single straightforward command | 4, 7 | `docker compose up --build` |
| No tasks lost / robustness | 3, 7 | terminal-state guarantee verified incl. worker-kill |

---

## Command cheat-sheet (for later phases)

```bash
# Build & run the whole stack
docker compose up --build

# Submit a task
curl -s -X POST localhost:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"qc": "OPENQASM 3.0; include \"stdgates.inc\"; qubit[2] q; bit[2] c; h q[0]; cx q[0], q[1]; c[0]=measure q[0]; c[1]=measure q[1];"}'

# Poll a task
curl -s localhost:8000/tasks/<task_id>

# Scale workers / run tests
docker compose up --scale worker=3
pytest
```

> Build phases in dependency order (0→7). The fastest path to a demoable system
> is Phases 0→4 (a working stack), then 5 (tests), then 6 (README), then 7
> (acceptance).
