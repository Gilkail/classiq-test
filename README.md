# Quantum Circuit Execution Engine

A production-style service that accepts a serialized **OpenQASM 3** circuit,
executes it **asynchronously** on a Qiskit simulator, and lets a client poll for
the result by task ID. Submission never blocks on execution, accepted tasks are
never lost, and the whole stack comes up with a single command.

> Design rationale lives in [`docs/REFERENCE.md`](./docs/REFERENCE.md); the
> phase-by-phase build log is in [`docs/IMPLEMENTATION_PLAN.md`](./docs/IMPLEMENTATION_PLAN.md).
> This README documents the system as built.

---

## Architecture

Three independently containerized services share one Redis instance. The **API**
validates and enqueues; **Redis** is both the RQ broker and the task-state
store; the **worker** runs the circuit and writes the terminal result. The API
and worker are separate processes (not threads) so the heavy Qiskit/Aer stack
lives only in the worker image and a CPU-bound simulation can never starve the
API event loop.

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

**Data flow.** On `POST /tasks` the API mints `task_id = uuid4()`, writes
`task:{id}` as `pending` **before** enqueuing (so an immediate `GET` always sees
`pending`), then enqueues an RQ job with `job_id == task_id`. The worker pops the
job, deserializes the QASM with `qiskit.qasm3.loads`, transpiles and runs it on
`AerSimulator`, and writes `task:{id} = completed` (with a counts dict) or
`error`. `GET /tasks/{id}` reads only the `task:{id}` hash and maps it to JSON —
it has zero coupling to RQ internals.

| Service  | Image            | Responsibility                                   |
|----------|------------------|--------------------------------------------------|
| `redis`  | `redis:7-alpine` | RQ broker + `task:{id}` state store, AOF on      |
| `api`    | `./api`          | FastAPI/Uvicorn HTTP surface, published on `:8000` |
| `worker` | `./worker`       | RQ worker running the Qiskit execution service   |

---

## Setup & local execution

**Prerequisites:** Docker and Docker Compose v2. Nothing else — Python, Redis,
and Qiskit all run inside the containers.

```bash
docker compose up --build
```

That single command builds all three images and starts the stack. `api` and
`worker` are **health-gated**: they only start after `redis` reports healthy, so
clients never hit a half-initialized stack. The API is published on
`http://localhost:8000`; interactive OpenAPI docs are served at
[`/docs`](http://localhost:8000/docs) (Swagger) and `/redoc`.

Configuration is environment-driven with defaults baked into the images, so the
stack runs correctly with **no `.env` file**. To override any default, copy the
template and edit it:

```bash
cp .env.example .env
```

| Variable        | Default                    | Used by        | Meaning                                  |
|-----------------|----------------------------|----------------|------------------------------------------|
| `REDIS_URL`     | `redis://redis:6379/0`     | api, worker    | Redis connection string                  |
| `RQ_QUEUE_NAME` | `default`                  | api, worker    | RQ queue name                            |
| `NUM_SHOTS`     | `1024`                     | worker         | Simulator shot count                     |
| `JOB_TIMEOUT`   | `180` (s)                  | api, worker    | Hard per-job ceiling                     |
| `RESULT_TTL`    | `86400` (s)                | api, worker    | TTL on `task:{id}` and RQ result metadata |
| `MAX_RETRIES`   | `2`                        | api            | RQ `Retry` attempts for transient faults |
| `LOG_LEVEL`     | `INFO`                     | all            | Logging verbosity                        |

**Scale workers** against the same queue with no code change:

```bash
docker compose up --build --scale worker=3
```

---

## API contract

Liveness probe: `GET /health` → `{"status": "ok"}` (returns `503` if Redis is
unreachable).

### `POST /tasks`

Submit a circuit for asynchronous execution.

**Request** — `Content-Type: application/json`

```json
{ "qc": "<serialized OpenQASM 3 string>" }
```

The body must be a JSON object with exactly the key `qc`, a non-empty string.
Unknown/extra fields are rejected.

| Code              | When                                                       | Body                                                                       |
|-------------------|------------------------------------------------------------|----------------------------------------------------------------------------|
| `202 Accepted`    | Valid payload, task queued                                 | `{"task_id": "<uuid>", "message": "Task submitted successfully."}`         |
| `400 Bad Request` | Missing `qc`, wrong type, empty string, extra key, bad JSON | `{"status": "error", "message": "<validation detail>"}`                    |
| `503`             | Redis/enqueue unreachable                                  | `{"status": "error", "message": "Task queue unavailable, retry later."}`  |

> **Why `202`, not `200`:** submission is *accepted for async processing*, so
> `202 Accepted` is the semantically correct code. The response body matches the
> assignment's example (`task_id` + message); validation happens at the API edge
> so bad input never reaches the worker or consumes a queue slot.

### `GET /tasks/{id}`

Retrieve the status/result of a task.

| Code            | Condition       | Body                                                              |
|-----------------|-----------------|------------------------------------------------------------------|
| `200 OK`        | Completed       | `{"status": "completed", "result": {"00": 512, "11": 512}}`      |
| `200 OK`        | Still processing | `{"status": "pending", "message": "Task is still in progress."}` |
| `200 OK`        | Failed          | `{"status": "error", "message": "<reason>"}`                     |
| `404 Not Found` | Unknown ID      | `{"status": "error", "message": "Task not found."}`              |

---

## Resiliency & failure modes

The headline guarantee is **at-least-once execution ending in a terminal
state**: every task the API accepts (`202`) becomes either `completed` or
`error` in finite time — none vanish silently or hang in `pending` forever.

- **Durability at submission.** RQ's `enqueue` persists the job inside Redis
  before `POST` returns `202`. Redis runs with **AOF persistence**
  (`--appendonly yes`) backed by a named Docker volume, so queued jobs and task
  state survive a Redis restart.
- **Crash recovery in flight.** Each job runs in a forked work-horse registered
  in RQ's `StartedJobRegistry`. If a worker is killed mid-job, RQ's maintenance
  pass routes the abandoned job to the `FailedJobRegistry`; `job_timeout` bounds
  how long a wedged simulation can run; `Retry(max=MAX_RETRIES)` retries
  transient faults.
- **Terminal-state guarantee in the worker.** The job function wraps *all*
  execution in `try/except` and writes a terminal `error` state for every caught
  failure (it deliberately does **not** re-raise), so a polling client always
  converges to an answer.

| Failure                                            | Handling                                                                                   | Client sees                                                     |
|----------------------------------------------------|--------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| Malformed body (missing/empty `qc`, extra key, bad JSON) | Pydantic rejects at the API edge before enqueue                                       | `400` + validation detail                                      |
| Invalid QASM3 syntax                               | `QASM3ImporterError` → terminal `error`, `error_kind=invalid_qasm3` (logged `WARNING`)     | `200` `{status: error, message: "Invalid QASM3 syntax"}`       |
| Simulation fault (too deep, OOM, Aer crash)        | Broad `except` → terminal `error`, `error_kind=execution_error` (logged `ERROR` + traceback) | `200` `{status: error, message: <reason>}`                   |
| Worker killed mid-job                              | RQ abandoned-job cleanup + `job_timeout`                                                    | converges to `error` (never stuck `pending`)                   |
| Unknown task ID on `GET`                          | `task:{id}` hash absent                                                                     | `404` `{status: error, message: "Task not found."}`            |
| Redis unavailable at submission                   | Roll back the pending hash, fail fast                                                       | `503` retryable error                                          |
| Startup race (api/worker boot before Redis ready) | Compose health gating: `depends_on: condition: service_healthy`                            | clients never hit a half-initialized stack                     |

Logs are structured JSON to stdout; every line in the request/execution path
carries the `task_id` so a single task can be traced end-to-end across the API
and worker containers.

---

## Usage examples

With the stack running (`docker compose up --build`):

**Submit a Bell-state circuit** and capture the task ID:

```bash
TASK_ID=$(curl -s -X POST localhost:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"qc": "OPENQASM 3.0; include \"stdgates.inc\"; qubit[2] q; bit[2] c; h q[0]; cx q[0], q[1]; c[0]=measure q[0]; c[1]=measure q[1];"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["task_id"])')
echo "$TASK_ID"
```

**Poll for the result:**

```bash
curl -s localhost:8000/tasks/$TASK_ID
# → {"status": "pending", "message": "Task is still in progress."}
# … shortly after …
# → {"status": "completed", "result": {"00": 512, "11": 512}}
```

A Bell state yields counts concentrated on `00` and `11`, summing to
`NUM_SHOTS`.

### Running the integration tests

The tests in `tests/` drive the real HTTP surface, so the stack must be up
first. Install the test deps and run pytest against the exposed port:

```bash
docker compose up --build -d           # start the stack in the background
pip install -r tests/requirements.txt  # pytest + httpx (in a venv if you prefer)
pytest                                  # green when submit → process → retrieve works
```

`BASE_URL` defaults to `http://localhost:8000` and can be overridden via the
environment. The suite covers submission validation (`test_submit.py`), the full
submit → poll → `completed` lifecycle (`test_lifecycle.py`), and the failure
paths — invalid QASM3 and unknown ID (`test_failures.py`).

---

## Make targets

A `Makefile` wraps the common commands:

| Target       | Action                                          |
|--------------|-------------------------------------------------|
| `make up`    | `docker compose up --build` (whole stack)       |
| `make down`  | `docker compose down` (stop; add `-v` to wipe Redis data) |
| `make test`  | start the stack detached, then run `pytest`     |
| `make logs`  | tail logs from all services                     |
