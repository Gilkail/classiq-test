# Quantum Circuit Execution Engine — developer shortcuts (Phase 6).
# Usage: `make up`, `make down`, `make test`, `make logs`.

.PHONY: up up-debug down test logs

# Build and run the whole stack (redis + api + worker), health-gated.
up:
	docker compose up --build

# Same as `up`, but exposes debugpy on api :5678 and worker :5679 for attach debugging.
up-debug:
	DEBUGPY_ENABLE=1 docker compose up --build

# Stop the stack. Append ARGS="-v" to also remove the Redis data volume:
#   make down ARGS="-v"
down:
	docker compose down $(ARGS)

# Host-side virtualenv for the integration tests (pytest + httpx).
# Kept local to the repo and out of git (see .gitignore).
VENV   := .venv
PIP    := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

# Create the venv and install the pinned test deps on first use, and again
# whenever tests/requirements.txt changes. Run via absolute path so a bare
# `pytest` on (or missing from) your PATH never matters.
$(PYTEST): tests/requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r tests/requirements.txt

# Bring the stack up detached, then run the integration tests against :8000.
test: $(PYTEST)
	docker compose up --build -d --wait
	$(PYTEST)

# Tail logs from all services (Ctrl-C to stop).
logs:
	docker compose logs -f
