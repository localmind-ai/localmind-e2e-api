#!/usr/bin/env bash
set -e
cd /home/localmind-e2e-api

# use Poetry to launch Gunicorn/Uvicorn
exec /home/localmind/.local/bin/poetry run gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --workers 4 \
  --bind 0.0.0.0:8000
