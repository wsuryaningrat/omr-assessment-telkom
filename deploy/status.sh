#!/usr/bin/env bash
# Ringkasan kondisi server: kontainer, kesehatan, memori, disk, error terbaru.
cd "$(dirname "$0")/.."
DC="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
echo "== Kontainer"; $DC ps
echo; echo "== Kesehatan API"; $DC exec -T api python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).read().decode())" 2>&1 | tail -1
echo; echo "== Memori/CPU kontainer"; docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' 2>/dev/null
echo; echo "== RAM & swap host"; free -h | sed -n '1,3p'
echo; echo "== Disk"; df -h / | tail -1; echo "Volume upload: $(docker system df -v 2>/dev/null | awk '/ljk_uploads/{print $NF}')"
echo; echo "== Error 30 menit terakhir (API)"; $DC logs --since 30m api 2>&1 | grep -iE "error|traceback|exception" | tail -8 || true
echo; echo "== Cadangan terbaru"; ls -1t ${BACKUP_DIR:-/var/backups/ljk}/ljk-*.sql.gz 2>/dev/null | head -3 || echo "(belum ada)"
