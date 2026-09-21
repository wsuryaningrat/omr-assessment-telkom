#!/usr/bin/env bash
# Pulihkan database dari cadangan. MENGGANTI seluruh data saat ini.
#   bash deploy/restore.sh /var/backups/ljk/ljk-20260922-020000.sql.gz
set -euo pipefail
cd "$(dirname "$0")/.."
F=${1:?pakai: bash deploy/restore.sh <berkas.sql.gz>}
[ -s "$F" ] && gzip -t "$F" || { echo "Berkas tidak ada / rusak: $F"; exit 1; }
DC="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
read -r -p "Ini MENGHAPUS data saat ini dan menggantinya dengan $F. Ketik 'ya' untuk lanjut: " a
[ "$a" = "ya" ] || { echo "Dibatalkan."; exit 1; }
$DC stop api
$DC exec -T db psql -U omr -d omr -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
gunzip -c "$F" | $DC exec -T db psql -U omr -d omr -v ON_ERROR_STOP=1 -q
$DC start api
echo "Selesai. Cek: bash deploy/status.sh"
