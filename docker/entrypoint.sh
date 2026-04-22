#!/bin/sh
set -e

WORKERS=${WORKERS:-2}
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8000}

echo "Starting llm-router: host=${HOST} port=${PORT} workers=${WORKERS}"

exec uvicorn llm_router.main:app \
    --host "${HOST}" \
    --port "${PORT}" \
    --workers "${WORKERS}" \
    --http h11
