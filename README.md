# Quantum Circuit Execution Engine

Submit an OpenQASM 3 circuit, get back a task ID, and poll until the result is ready. Execution runs in the background on a Qiskit simulator.

## Quick start

You need Docker and Docker Compose v2. Python, Redis, and Qiskit all run inside the containers.

**1. Start the stack**

```bash
docker compose up --build
```

The API listens on `http://localhost:8000`.

**2. Health check**

```bash
curl localhost:8000/health
# {"status": "ok"}
```

**3. Submit a circuit** (2-qubit Bell state)

```bash
TASK_ID=$(curl -s -X POST localhost:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"qc": "OPENQASM 3.0; include \"stdgates.inc\"; qubit[2] q; bit[2] c; h q[0]; cx q[0], q[1]; c[0]=measure q[0]; c[1]=measure q[1];"}' \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["task_id"])')
echo "$TASK_ID"
```

**4. Poll for the result**

```bash
curl -s localhost:8000/tasks/$TASK_ID
# {"status": "pending", "message": "Task is still in progress."}
# then
# {"status": "completed", "result": {"00": 512, "11": 512}}
```

A Bell state should show counts on `00` and `11` that add up to 1024 shots (the default).

API docs: [`/docs`](http://localhost:8000/docs) (Swagger) and `/redoc`.

Stop the stack with `Ctrl+C` or `docker compose down`.

### Scale workers

```bash
docker compose up --build --scale worker=3
```

### Configuration

Defaults are built into the images. To override them, copy `.env.example` to `.env` and edit values there.

| Variable        | Default                | Meaning                    |
|-----------------|------------------------|----------------------------|
| `REDIS_URL`     | `redis://redis:6379/0` | Redis connection           |
| `RQ_QUEUE_NAME` | `default`              | RQ queue name              |
| `NUM_SHOTS`     | `1024`                 | Simulator shots            |
| `JOB_TIMEOUT`   | `180`                  | Max job runtime (seconds)  |
| `RESULT_TTL`    | `86400`                | Task result TTL (seconds)  |
| `MAX_RETRIES`   | `2`                    | RQ retry count             |
| `LOG_LEVEL`     | `INFO`                 | Log level                  |

### Make targets

| Target      | What it does                          |
|-------------|---------------------------------------|
| `make up`   | Start the stack                       |
| `make down` | Stop the stack                        |
| `make test` | Start the stack and run pytest        |
| `make logs` | Follow logs from all services         |

## API

### `GET /health`

Returns `{"status": "ok"}`. Returns `503` if Redis is down.

### `POST /tasks`

Submit a circuit.

Request body:

```json
{ "qc": "<OpenQASM 3 string>" }
```

| Status | When |
|--------|------|
| `202`  | Task accepted and queued |
| `400`  | Invalid request (missing `qc`, bad JSON, extra fields) |
| `503`  | Redis or queue unavailable |

Response on success:

```json
{"task_id": "<uuid>", "message": "Task submitted successfully."}
```

### `GET /tasks/{id}`

| Status | When |
|--------|------|
| `200`  | Task completed, pending, or failed (see `status` field) |
| `404`  | Unknown task ID |

Completed example:

```json
{"status": "completed", "result": {"00": 512, "11": 512}}
```

Pending example:

```json
{"status": "pending", "message": "Task is still in progress."}
```

Error example:

```json
{"status": "error", "message": "<reason>"}
```

## Architecture

Three services share one Redis instance:

- **api** (FastAPI): validates requests, creates tasks, enqueues jobs
- **redis**: message queue and task state store
- **worker** (RQ + Qiskit): runs circuits on AerSimulator

```
Client
  |  POST /tasks
  v
api  -->  redis  -->  worker
  ^                          |
  |  GET /tasks/{id}         |
  +--------------------------+
         (writes result to redis)
```

On submit, the API writes a `pending` task record, then enqueues an RQ job with the same ID. The worker runs the circuit and updates the task to `completed` or `error`. Poll endpoints read task state from Redis only.

## Failure handling

- Bad JSON or missing `qc` returns `400` before anything is queued.
- Invalid QASM returns `200` with `status: error`.
- Simulation errors return `200` with `status: error`.
- If Redis is down at submit time, the API returns `503`.
- Redis uses AOF persistence so queued jobs survive a restart.
- Jobs have a timeout and limited retries so tasks do not stay `pending` forever.

## Tests

The integration tests hit the real HTTP API. Start the stack first:

```bash
docker compose up --build -d
pip install -r tests/requirements.txt
pytest
```

Or run `make test`, which starts the stack and runs pytest for you.

Set `BASE_URL` to point at a different host (default is `http://localhost:8000`).

Test files:

- `tests/test_submit.py` - request validation
- `tests/test_lifecycle.py` - submit, poll, completed
- `tests/test_failures.py` - invalid QASM and unknown task ID
