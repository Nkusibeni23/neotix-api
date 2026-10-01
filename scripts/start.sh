#!/bin/sh
# Container entrypoint: every step is idempotent, so restarts are safe.
set -e
alembic upgrade head
python -m app.seed
python -m app.importer seed/episodes.csv
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
