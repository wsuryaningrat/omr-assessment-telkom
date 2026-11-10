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
echo "==> Kode LJK v2 (branch v2) di ../ljk-v2"
git fetch -q origin +v2:refs/remotes/origin/v2
if [ -d ../ljk-v2 ]; then git -C ../ljk-v2 checkout -q --detach origin/v2; else git worktree add -q --detach ../ljk-v2 origin/v2; fi
grep -q '^V2_SYNC_TOKEN=.\+' deploy/.env || { echo "V2_SYNC_TOKEN=$(openssl rand -hex 24)" >> deploy/.env; echo "   (V2_SYNC_TOKEN dibuat)"; }
echo "==> Build & jalankan ($PREV -> $NEW)"
$DC up -d --build
echo "==> Tunggu API sehat"
for i in $(seq 1 30); do
  st=$($DC ps --format '{{.Service}} {{.Health}}' | awk '$1=="api"||$1=="ljk-v2"{print $2}' | sort -u | tr '\n' ' ')
  [ "$st" = "healthy " ] && { echo "API sehat."; $DC ps; docker image prune -f >/dev/null; exit 0; }
  sleep 3
done
echo "API TIDAK sehat setelah 90 dtk. Log terakhir:"; $DC logs --tail 40 api
echo "Rollback:  git checkout $PREV && $DC up -d --build"
exit 1
