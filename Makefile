# Quantum Circuit Execution Engine — developer shortcuts (Phase 6).
# Usage: `make up`, `make down`, `make test`, `make logs`.

.PHONY: up down test logs

# Build and run the whole stack (redis + api + worker), health-gated.
up:
	docker compose up --build

# Stop the stack. Append ARGS="-v" to also remove the Redis data volume:
#   make down ARGS="-v"
down:
	docker compose down $(ARGS)

# Bring the stack up detached, then run the integration tests against :8000.
test:
	docker compose up --build -d
	pytest

# Tail logs from all services (Ctrl-C to stop).
logs:
	docker compose logs -f
