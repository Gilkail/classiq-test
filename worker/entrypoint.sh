#!/bin/sh
set -e

if [ "${DEBUGPY_ENABLE}" = "1" ]; then
  exec python -m debugpy --listen 0.0.0.0:5679 -m app.worker
fi

exec python -m app.worker
