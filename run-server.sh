#!/usr/bin/env bash
# Jalankan server lokal (SQLite, tanpa instalasi database). Buka http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3.11}
[ -d .venv-server ] || { $PY -m venv .venv-server && .venv-server/bin/pip install -q -r server/requirements.txt; }
export DATABASE_URL=${DATABASE_URL:-sqlite:///./data/omr.db}
export UPLOAD_DIR=${UPLOAD_DIR:-./data/uploads}
export ADMIN_TOKEN=${ADMIN_TOKEN:-dev-admin}
export SCAN_WORKERS=${SCAN_WORKERS:-2}
mkdir -p data
exec .venv-server/bin/uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
