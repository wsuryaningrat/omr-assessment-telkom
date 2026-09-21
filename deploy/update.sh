#!/usr/bin/env bash
# Perbarui aplikasi ke versi terbaru di branch saat ini, dengan cadangan otomatis dan pengecekan kesehatan.
#   bash deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DC="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
PREV=$(git rev-parse --short HEAD)

echo "==> Cadangan sebelum update"; bash deploy/backup.sh || echo "(cadangan dilewati)"
echo "==> Ambil kode terbaru"; git pull --ff-only
NEW=$(git rev-parse --short HEAD)
echo "==> Build & jalankan ($PREV -> $NEW)"
$DC up -d --build
echo "==> Tunggu API sehat"
for i in $(seq 1 30); do
  st=$($DC ps --format '{{.Service}} {{.Health}}' | awk '$1=="api"{print $2}')
  [ "$st" = "healthy" ] && { echo "API sehat."; $DC ps; docker image prune -f >/dev/null; exit 0; }
  sleep 3
done
echo "API TIDAK sehat setelah 90 dtk. Log terakhir:"; $DC logs --tail 40 api
echo "Rollback:  git checkout $PREV && $DC up -d --build"
exit 1
