#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Applying database migrations..."
  if ! alembic upgrade head; then
    echo "WARNING: alembic migration failed; falling back to AUTO_CREATE_SCHEMA on startup."
  fi
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
