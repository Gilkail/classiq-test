#!/bin/sh
set -e

if [ "${DEBUGPY_ENABLE}" = "1" ]; then
  exec python -m debugpy --listen 0.0.0.0:5678 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
