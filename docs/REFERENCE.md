# Reference Architecture — Quantum Circuit Execution Engine

> **Status:** Design baseline (pre-implementation)
> **Audience:** Implementers and reviewers
> **Companion document:** [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md)

This document is the single source of truth for the system's architecture, data
model, API contract, and failure handling. It is written so that implementation
can proceed phase-by-phase from the companion plan without re-deciding any
architectural question. Where a decision was made, the rationale is recorded
inline so reviewers can judge the trade-offs.

---

## 1. Problem Statement & Goals

Build a production-grade service that accepts a serialized quantum circuit
(OpenQASM 3), executes it asynchronously on a simulator, and lets a client poll
for the result by task ID.

The architecture is organized around four core properties:

| Core property | Where it is addressed |
|---|---|
| **Asynchronous processing** | API never blocks on execution; a separate worker process runs the circuit (§4, §5). |
| **Task integrity ("no tasks lost")** | Durable Redis-backed queue + AOF persistence + RQ crash-recovery registries + terminal-state guarantee (§7). |
| **Containerization & orchestration** | Three services in `docker-compose.yml` with health-gated startup ordering (§9). |
| **Robustness** | Strict input validation, exhaustive worker `try/except`, structured logging, no task can hang in `pending` forever (§8). |

### Non-goals (out of scope)

Authentication/authorization, multi-tenant quotas, horizontal autoscaling, a
durable SQL store, real-quantum-hardware backends, and a result-streaming
(WebSocket) channel are out of scope. The design leaves clean seams for each;
see §11.

---

## 2. Technology Stack & Rationale

| Layer | Choice | Why |
|---|---|---|
| Language / base image | **Python 3.10** (`python:3.10-slim`) | Meets the 3.9+ baseline with a small footprint; 3.10 has mature typing + structural pattern matching. |
| Web framework | **FastAPI + Uvicorn** | Native `async` I/O, automatic Pydantic request validation, and auto-generated OpenAPI/Swagger docs at `/docs`. |
| Queue **and** state store | **Redis** (double duty) | Acts as both the RQ message broker **and** the key/value store for task state. One dependency, minimal container footprint, and the queue + result write can share a connection. |
| Task manager | **RQ (Redis Queue)** | Low-boilerplate, Redis-native. Ships crash-recovery primitives out of the box: `StartedJobRegistry`, `FailedJobRegistry`, `AbandonedJobError` cleanup, job timeouts, and a `Retry` policy. (Selected over a hand-rolled `BLMOVE` loop for speed of delivery, and over Celery to avoid its heavier footprint.) |
| Quantum execution | **Qiskit 2.x + qiskit-aer** (`AerSimulator`) | Reference simulator for this project. Deserialization via `qiskit.qasm3.loads`. |
| QASM3 import | **qiskit-qasm3-import ≥ 0.6.0** | `qiskit.qasm3.loads()` is a thin wrapper over `qiskit_qasm3_import.parse`; the package must be installed explicitly. Raises `QASM3ImporterError` on malformed input — our primary validation hook in the worker. |
| Tests | **pytest + httpx** | Integration tests drive the real HTTP surface exposed by Docker Compose, exercising the full client lifecycle. |

**Verified API facts (current as of June 2026):**

- `qiskit.qasm3.loads(program: str) -> QuantumCircuit` — wraps `qiskit_qasm3_import.parse`; requires `qiskit-qasm3-import>=0.6.0`; raises `QASM3ImporterError` on failure. A faster `loads_experimental()` exists with a reduced feature set (not used; we favor full-feature correctness).
- `qiskit-aer` latest is 0.17.x and requires Qiskit ≥ 1.1.0; import is `from qiskit_aer import AerSimulator`.
- Execution path: `transpile(qc, simulator)` → `simulator.run(transpiled, shots=NUM_SHOTS)` → `job.result().get_counts()`.

> **Version pinning policy:** the implementation phase pins exact versions in
> each service's `requirements.txt` and rebuilds to confirm compatibility. The
> worker and API have **separate** dependency files so the API image never
> compiles the heavy Qiskit/Aer stack.

---

## 3. Component Overview

Three independently containerized services share one Redis instance.

```
                       ┌──────────────────────────────────────────┐
                       │                Client                     │
                       │   (curl / pytest+httpx / any HTTP caller) │
                       └───────────────┬───────────────┬──────────┘
                    POST /tasks (202)  │               │  GET /tasks/{id} (200/404)
                                       ▼               ▼
                       ┌──────────────────────────────────────────┐
                       │            api  (FastAPI + Uvicorn)       │
                       │  • validates {"qc": "<str>"} (Pydantic)   │
                       │  • mints task_id (UUID4)                  │
                       │  • writes task:{id} = pending             │
                       │  • enqueues RQ job (job_id = task_id)     │
                       │  • reads task:{id} on GET                 │
                       └───────────────┬──────────────────────────┘
                                       │ enqueue / read-write
                                       ▼
                       ┌──────────────────────────────────────────┐
                       │                 redis                     │
                       │  • RQ queue + job registries (transport)  │
                       │  • task:{id} HASH  (canonical state)      │
                       │  • AOF persistence (durability)           │
                       └───────────────┬──────────────────────────┘
                                       │ pop job / write result
                                       ▼
                       ┌──────────────────────────────────────────┐
                       │           worker  (RQ worker)             │
                       │  • qiskit.qasm3.loads(qc)                 │
                       │  • transpile + AerSimulator.run(shots)    │
                       │  • writes task:{id} = completed | error   │
                       └──────────────────────────────────────────┘
```

**Why the API and worker are separate services (not threads):** it lets the
heavy Qiskit/Aer dependency tree live only in the worker image, lets the two be
scaled and resourced independently, and means a CPU-bound simulation can never
starve the API event loop.

---

## 4. Data Flow

### 4.1 `POST /tasks` — submission (write path)

```
Client                api                         redis
  │  POST {"qc": ...}   │                            │
  │────────────────────▶│                            │
  │                     │ Pydantic validates body    │
  │                     │  (400 on malformed input)  │
  │                     │ task_id = uuid4()          │
  │                     │ HSET task:{id} status=pending, created_at
  │                     │───────────────────────────▶│
  │                     │ RQ enqueue(job_id=task_id) │
  │                     │───────────────────────────▶│  (durable commit point)
  │   202 + task_id     │                            │
  │◀────────────────────│                            │
```

Ordering rationale: the `task:{id}` hash is written **before** enqueue so a
`GET` issued immediately after submission always sees `pending`. If `enqueue`
fails, the hash is rolled back and the client receives `503` (no orphaned
state). The residual crash window (hash written, process dies before enqueue) is
addressed in §7.3.

### 4.2 Background worker — execution

```
worker (RQ)                                   redis
  │  blocking pop next job (BLPOP on queue)     │
  │◀────────────────────────────────────────────│
  │  registered in StartedJobRegistry           │
  │  qc = qiskit.qasm3.loads(payload["qc"])      │   ── QASM3ImporterError ─┐
  │  transpiled = transpile(qc, AerSimulator())  │   ── any Exception ──────┤
  │  counts = run(transpiled, shots).get_counts()│                          │
  │  HSET task:{id} status=completed, result=... │                          │
  │─────────────────────────────────────────────▶│                          │
  │                                              │   on caught failure:     │
  │  HSET task:{id} status=error, message=...    │◀─────────────────────────┘
```

The job function **never propagates** an exception for an expected failure mode
(bad QASM3, simulation fault); it converts it to a terminal `error` state. RQ's
`FailedJobRegistry` remains a backstop for *unexpected* crashes (e.g., the
worker process is killed mid-job) — see §7.

### 4.3 `GET /tasks/{id}` — polling (read path)

```
Client                 api                       redis
  │ GET /tasks/{id}      │                          │
  │─────────────────────▶│ HGETALL task:{id}        │
  │                      │─────────────────────────▶│
  │                      │◀─────────────────────────│
  │  map state → JSON    │                          │
  │◀─────────────────────│                          │
        completed → 200 {status, result}
        pending   → 200 {status, message}
        error     → 200 {status, message}
        (missing) → 404 {status: error, message: "Task not found."}
```

`GET` reads **only** the `task:{id}` hash — it has zero coupling to RQ
internals, keeping the public contract stable regardless of the queue
technology.

---

## 5. Redis Data Model

### 5.1 Canonical task state — `task:{task_id}` (HASH)

| Field | Type | Set by | Description |
|---|---|---|---|
| `status` | string | api → worker | `pending` \| `completed` \| `error`. |
| `result` | JSON string | worker | Counts dict, e.g. `{"00": 512, "11": 512}` (present when `completed`). |
| `message` | string | api / worker | Human-readable note (progress text, or failure reason). |
| `error_kind` | string | worker | Machine-readable category: `invalid_qasm3` \| `execution_error` (present when `error`). |
| `shots` | int | api/worker | Shot count used for the run (config echo). |
| `created_at` | ISO-8601 | api | Submission timestamp. |
| `updated_at` | ISO-8601 | worker | Last state transition. |

- **Key namespace:** `task:` prefix isolates application state from RQ's own
  `rq:*` keys.
- **TTL:** the hash is given an expiry (default 24h, configurable) so completed
  and errored results self-evict and Redis memory stays bounded. Pending tasks
  are refreshed on transition.
- **`result` is JSON-encoded** because Redis hash values are strings; the API
  decodes it back to an object before returning it.

### 5.2 Queue/transport keys — owned by RQ (`rq:*`)

RQ manages these; the application does not read or write them directly. They are
listed for operational awareness:

- `rq:queue:default` — the pending job list.
- `rq:job:{task_id}` — per-job hash (status, function, args, `exc_info`).
- `rq:wip:*`, `StartedJobRegistry`, `FinishedJobRegistry`, `FailedJobRegistry` —
  in-flight / terminal registries used for crash recovery (§7).

### 5.3 Identity & idempotency

The application mints `task_id = uuid4()` and passes it to RQ as the explicit
`job_id`. Consequences: (1) one ID threads through logs end-to-end; (2)
enqueue is **idempotent** — re-enqueuing the same `task_id` while the job exists
will not create a duplicate, which makes the §7.3 recovery safe.

---

## 6. API Contract Specification

Interactive OpenAPI docs are auto-served at `GET /docs` (Swagger) and
`GET /redoc`. A liveness probe is exposed at `GET /health`.

### 6.1 `POST /tasks`

Submit a circuit for asynchronous execution.

**Request**

```
Content-Type: application/json

{ "qc": "<serialized OpenQASM 3 string>" }
```

| Constraint | Rule |
|---|---|
| Body shape | Must be a JSON object with exactly the key `qc`. |
| `qc` type | Non-empty string. Extra/unknown fields are rejected. |

**Responses**

| Code | When | Body |
|---|---|---|
| `202 Accepted` | Valid payload, task queued | `{"task_id": "<uuid>", "message": "Task submitted successfully."}` |
| `400 Bad Request` | Missing `qc`, wrong type, empty string, or malformed JSON | `{"status": "error", "message": "<validation detail>"}` |
| `503 Service Unavailable` | Redis/enqueue unreachable | `{"status": "error", "message": "Task queue unavailable, retry later."}` |

> **Status-code note:** the response body (`task_id` + message) is returned with
> HTTP `202 Accepted` rather than `200`, which is the semantically correct code
> for "accepted for async processing." This is called out in the README so the
> choice is explicit, not accidental.

**Validation happens at the API edge.** Malformed payloads are rejected *before*
anything is enqueued, so bad input never reaches the worker or consumes a queue
slot.

### 6.2 `GET /tasks/{id}`

Retrieve the status/result of a task.

**Responses**

| Code | Condition | Body |
|---|---|---|
| `200 OK` | Completed | `{"status": "completed", "result": {"00": 512, "11": 512}}` |
| `200 OK` | Still processing | `{"status": "pending", "message": "Task is still in progress."}` |
| `200 OK` | Failed | `{"status": "error", "message": "<reason>"}` |
| `404 Not Found` | Unknown ID | `{"status": "error", "message": "Task not found."}` |

The three status strings (`completed` / `pending` / `error`) and the
`result` shape are the stable public contract clients depend on.

---

## 7. Task Integrity — "No Tasks Lost"

This is the headline requirement. The guarantee is **at-least-once execution
ending in a terminal state**: every task that the API accepts (202) is either
`completed` or `error` in finite time; none vanish silently or hang in `pending`
forever.

### 7.1 Durability at submission

- RQ's `enqueue` writes the job hash **and** pushes onto the queue list inside
  Redis before returning. Once `POST` responds 202, the job is persisted, not
  just in memory.
- Redis runs with **AOF persistence** (`appendonly yes`, `appendfsync
  everysec`). A Redis container restart replays the log, so queued-but-unstarted
  jobs and task state survive a crash with at most ~1s of writes at risk.
- A named Docker volume backs Redis data so it persists across `docker-compose
  down`/`up` of the Redis container.

### 7.2 Crash recovery in flight

RQ executes each job in a forked "work-horse" child and registers it in the
`StartedJobRegistry`. If a worker (or its host) is killed mid-execution, the job
does **not** disappear: RQ's maintenance pass detects the abandoned entry,
raises `AbandonedJobError`, and routes it to the `FailedJobRegistry`. We
configure:

- **`job_timeout`** — a hard ceiling per job, so a wedged simulation is killed
  and surfaced as failed rather than running forever.
- **`Retry(max=N, interval=[...])`** — transient faults are retried with backoff
  before being considered terminal.
- **`result_ttl` / `failure_ttl`** — bound how long RQ keeps terminal job
  metadata.

### 7.3 Closing the API-side window

The one place a task could be "accepted but never queued" is a crash between the
`task:{id}=pending` write (§4.1) and `enqueue`. Mitigations, in order of
simplicity:

1. **Idempotent re-enqueue:** because `job_id == task_id`, a client retry (or a
   sweeper) can safely re-submit without creating duplicates.
2. **Pending sweeper (optional hardening, §11):** a periodic pass finds
   `task:{id}` hashes stuck in `pending` with **no** corresponding `rq:job:{id}`
   and re-enqueues them. Documented as the production-hardening path; not
   required for the baseline guarantee above.

### 7.4 Terminal-state guarantee in the worker

The worker's job function wraps *all* execution in `try/except` (§8) and writes
a terminal `error` state for every caught failure. Combined with `job_timeout`,
this means **no caught or timed-out task remains `pending`** — the polling
client always converges to a terminal answer.

---

## 8. Failure Modes & Handling

| # | Failure | Detection | Handling | Client sees |
|---|---|---|---|---|
| F1 | Malformed request body (missing `qc`, wrong type, empty, bad JSON, extra keys) | Pydantic model at API edge | Reject before enqueue | `400` + validation detail |
| F2 | Invalid QASM3 syntax | `QASM3ImporterError` from `qiskit.qasm3.loads` | `except QASM3ImporterError` → `task:{id}` = `error`, `error_kind=invalid_qasm3`, log `"Invalid QASM3 syntax"` | `200` `{status: error, message: "Invalid QASM3 syntax"}` |
| F3 | Simulation fault — circuit too deep, OOM, Aer crash | Broad `except Exception` around transpile/run | Catch, set `error`, `error_kind=execution_error`, log stack trace with `task_id` | `200` `{status: error, message: ...}` |
| F4 | Worker process killed mid-job | RQ `StartedJobRegistry` + `AbandonedJobError` cleanup | Job → `FailedJobRegistry`; `job_timeout` bounds hang time | converges to `error` (never stuck `pending`) |
| F5 | Unknown task ID on GET | `task:{id}` hash absent | Return not-found | `404` `{status: error, message: "Task not found."}` |
| F6 | Redis unavailable at submission | Connection error on HSET/enqueue | Roll back, fail fast | `503` retryable error |
| F7 | Startup race — API/worker boot before Redis is ready | Compose health gating | `depends_on: condition: service_healthy` + Redis healthcheck | clients never hit a half-initialized stack |

**Logging strategy.** Structured (JSON) logs to stdout (the 12-factor way;
Docker captures them). Every log line in the request/execution path carries the
`task_id` as a correlation key, so a single task can be traced end-to-end across
the API and worker containers. Levels: `INFO` for lifecycle transitions
(submitted, started, completed), `WARNING` for handled validation/execution
failures, `ERROR` (with traceback) for unexpected exceptions.

---

## 9. Containerization & Orchestration

Four Compose services (one is the datastore):

| Service | Image source | Responsibility | Depends on |
|---|---|---|---|
| `redis` | `redis:7-alpine` | Broker + state store, AOF on, named volume | — |
| `api` | `./api/Dockerfile` | FastAPI/Uvicorn HTTP surface; published on host `:8000` | `redis` (healthy) |
| `worker` | `./worker/Dockerfile` | RQ worker running the Qiskit execution service | `redis` (healthy) |

Key orchestration details:

- **Health-gated startup (F7):** `redis` declares a `healthcheck` (`redis-cli
  ping`); `api` and `worker` use `depends_on: { redis: { condition:
  service_healthy } }` so neither starts wiring up before Redis answers.
- **Separate Dockerfiles / requirements** keep the heavy Qiskit + Aer compile
  out of the API image (smaller, faster API builds and deploys).
- **Config via environment** (`REDIS_URL`, `RQ_QUEUE_NAME`, `NUM_SHOTS`,
  `JOB_TIMEOUT`, `RESULT_TTL`, `LOG_LEVEL`) injected by Compose, with sane
  defaults baked in.
- **Scalability seam:** `docker compose up --scale worker=N` runs N workers
  against the same queue with no code change.
- **Single-command runfile:** `docker compose up --build` brings up the whole
  stack with one straightforward command.

---

## 10. Configuration Reference

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | api, worker | Redis connection string. |
| `RQ_QUEUE_NAME` | `default` | api, worker | Name of the RQ queue. |
| `NUM_SHOTS` | `1024` | worker | Simulator shot count. |
| `JOB_TIMEOUT` | `180` (s) | api (enqueue), worker | Hard per-job ceiling. |
| `RESULT_TTL` | `86400` (s) | api, worker | TTL on `task:{id}` and RQ result metadata. |
| `MAX_RETRIES` | `2` | api (enqueue) | RQ `Retry` attempts for transient faults. |
| `LOG_LEVEL` | `INFO` | all | Logging verbosity. |

---

## 11. Future Hardening (Seams Left Intentionally Open)

- **Pending sweeper / reaper** (§7.3) to close the submission crash window
  fully.
- **Idempotency keys** on POST to dedupe client retries at the API layer.
- **Durable store** (Postgres) if results must outlive Redis memory/TTL.
- **AuthN/Z + rate limiting** at the API edge.
- **Result push** via WebSocket/Server-Sent Events to remove client polling.
- **Metrics** (Prometheus) on queue depth, job latency, failure rate.

---

## Appendix A — Worked Example (Bell State)

Input (`qc` value, OpenQASM 3):

```
OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c[0] = measure q[0];
c[1] = measure q[1];
```

Lifecycle:

1. `POST /tasks` → `202 {"task_id": "a1b2...", "message": "Task submitted successfully."}`
2. `GET /tasks/a1b2...` (immediately) → `200 {"status": "pending", "message": "Task is still in progress."}`
3. `GET /tasks/a1b2...` (after execution) → `200 {"status": "completed", "result": {"00": ~512, "11": ~512}}`

A Bell state yields counts concentrated on `00` and `11` (the `01`/`10`
outcomes appear only from statistical noise of finite shots — here, none).

## Appendix B — Requirements Traceability

| Requirement | Satisfied by |
|---|---|
| `POST /tasks` returns task_id + message | §6.1 |
| `GET /tasks/{id}` returns completed/pending/error/not-found | §6.2 |
| Asynchronous processing | §3, §4.2 (separate worker service + RQ) |
| No tasks lost | §7 (durability, crash recovery, terminal-state guarantee) |
| Docker Compose orchestrates all components | §9 |
| Error handling & logging | §8 |
| Python 3.9+ & lightweight framework | §2 (3.10-slim + FastAPI) |
| Integration tests (submit/process/retrieve) | see `IMPLEMENTATION_PLAN.md` Phase 5 |
| README with setup/design/usage | `IMPLEMENTATION_PLAN.md` Phase 6 |
