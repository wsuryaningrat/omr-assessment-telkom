#!/usr/bin/env bash
# Jalankan server lokal (SQLite, tanpa instalasi database). Buka http://localhost:8000
#   - Google Sheet: otomatis aktif bila .streamlit/secrets.toml ada (matikan dengan GSHEET_DISABLE=1)
#   - Token admin : acak, disimpan di data/.admin_token (atau isi env ADMIN_TOKEN sendiri)
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3.11}
[ -d .venv-server ] || { $PY -m venv .venv-server && .venv-server/bin/pip install -q -r server/requirements.txt; }
mkdir -p data
# pengaturan lokal & rahasia (MS_CLIENT_SECRET dll.) — berkas ini diabaikan git
[ -f .env.local ] && { set -a; . ./.env.local; set +a; }
export DATABASE_URL=${DATABASE_URL:-sqlite:///./data/omr.db}
export UPLOAD_DIR=${UPLOAD_DIR:-./data/uploads}
export SCAN_WORKERS=${SCAN_WORKERS:-2}

# --- token admin: jangan pakai nilai yang mudah ditebak (server terbuka di jaringan lokal)
if [ -z "${ADMIN_TOKEN:-}" ]; then
  [ -s data/.admin_token ] || { .venv-server/bin/python -c "import secrets;print(secrets.token_urlsafe(18))" > data/.admin_token; chmod 600 data/.admin_token; }
  ADMIN_TOKEN=$(cat data/.admin_token)
fi
export ADMIN_TOKEN

# --- Google Sheet: pakai kredensial Streamlit yang sudah ada (tidak disalin)
if [ "${GSHEET_DISABLE:-0}" != "1" ] && [ -z "${GSHEET_CREDENTIALS:-}" ] && [ -f .streamlit/secrets.toml ]; then
  export GSHEET_CREDENTIALS=.streamlit/secrets.toml
fi

echo "──────────────────────────────────────────────"
echo " Admin  : http://localhost:${PORT:-8000}/admin"
if [ -n "${MS_CLIENT_ID:-}" ] || [ -n "${GOOGLE_CLIENT_ID:-}" ]; then
  echo " Login  : $([ -n "${GOOGLE_CLIENT_ID:-}" ] && echo -n "Google ")$([ -n "${MS_CLIENT_ID:-}" ] && echo -n "Microsoft ")"
  echo " Admin  : ${ADMIN_EMAILS:-${ADMIN_DOMAINS:-BELUM ADA — semua akun ditolak}}"
else echo " Token  : ${ADMIN_TOKEN}"; fi
echo " Sheet  : $([ -n "${GSHEET_CREDENTIALS:-}" ] && echo "AKTIF (${GSHEET_CREDENTIALS})" || echo "nonaktif")"
echo "──────────────────────────────────────────────"
exec .venv-server/bin/uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
