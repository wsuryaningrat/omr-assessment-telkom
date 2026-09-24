#!/usr/bin/env bash
# Cadangan database (pg_dump terkompresi). Jalankan manual atau lewat cron (lihat docs/DEPLOY.md).
#   BACKUP_DIR=/var/backups/ljk KEEP_DAYS=7 bash deploy/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
BACKUP_DIR=${BACKUP_DIR:-/var/backups/ljk}
KEEP_DAYS=${KEEP_DAYS:-7}
mkdir -p "$BACKUP_DIR"
OUT="$BACKUP_DIR/ljk-$(date +%Y%m%d-%H%M%S).sql.gz"
DC="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"

$DC exec -T db pg_dump -U omr --no-owner omr </dev/null | gzip -9 > "$OUT.tmp"
# validasi: berkas tidak kosong & gzip utuh & memuat tabel utama.
# PENTING: pakai `grep -c ... >/dev/null` (membaca sampai habis), BUKAN `grep -q`: dengan `pipefail`,
# grep -q menutup pipa lebih awal -> gunzip kena SIGPIPE -> dump valid salah dianggap gagal.
[ -s "$OUT.tmp" ] && gzip -t "$OUT.tmp" && gunzip -c "$OUT.tmp" | grep -c "CREATE TABLE public.scan_session" >/dev/null \
  || { rm -f "$OUT.tmp"; echo "GAGAL: cadangan tidak valid" >&2; exit 1; }
mv "$OUT.tmp" "$OUT"
find "$BACKUP_DIR" -name 'ljk-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "OK $(du -h "$OUT" | cut -f1)  $OUT  (tersimpan: $(ls "$BACKUP_DIR"/ljk-*.sql.gz | wc -l) berkas)"
