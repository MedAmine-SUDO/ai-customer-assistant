#!/bin/sh
# Container entrypoint: migrate, optionally load demo data, then serve.
set -e
alembic upgrade head
if [ "$SEED_DEMO" = "true" ]; then
    # A failed seed (no OpenAI credit, network down) shouldn't keep the app from starting:
    # the dashboard still works, and the next start retries the documents that aren't embedded.
    python -m app.seed || echo "WARNING: seeding failed, starting anyway (see the error above)" >&2
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
