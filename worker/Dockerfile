# syntax=docker/dockerfile:1

# ── Worker service: RQ consumer running the Qiskit/Aer execution engine ──
# Build context is ./worker (see docker-compose.yml). This is the heavy image;
# qiskit / qiskit-aer / qiskit-qasm3-import ship cp310 manylinux wheels, so no
# compiler toolchain is needed — only the OpenMP runtime that Aer links against.
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# qiskit-aer's prebuilt wheels load libgomp at import time; it is not present
# in python:3.10-slim, so install it explicitly (avoids ImportError: libgomp.so.1).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first for layer caching (the Qiskit install is the slow step).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application package (run as `python -m app.worker`; resolves app.jobs.run_task).
COPY app ./app

# Drop root for runtime.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Blocking RQ worker loop (worker.py __main__ -> main()).
CMD ["python", "-m", "app.worker"]
