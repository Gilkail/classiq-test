# syntax=docker/dockerfile:1

# ── API service: FastAPI/Uvicorn HTTP ingress (REFERENCE §9) ──
# Build context is ./api (see docker-compose.yml). This image intentionally
# omits the Qiskit/Aer stack so it stays small and builds fast.
FROM python:3.10-slim

# Reproducible, quiet, unbuffered Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer caches across code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application package (uvicorn imports it as `app.main:app`).
COPY app ./app
COPY entrypoint.sh /entrypoint.sh

# Drop root for runtime.
RUN chmod +x /entrypoint.sh \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000 5678

ENTRYPOINT ["/entrypoint.sh"]
