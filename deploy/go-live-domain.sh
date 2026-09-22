#!/usr/bin/env bash
# Jalankan SETELAH DNS mathcenter.cswda.id -> 103.242.10.121 terkonfirmasi aktif.
# Mengaktifkan domain+HTTPS dan login Google-only untuk admin. Jalankan dari laptop:
#   bash deploy/go-live-domain.sh
set -euo pipefail
VPS=root@103.242.10.121
KEY=~/.ssh/ljk_vps
DOMAIN=mathcenter.cswda.id

echo "==> Cek DNS dari laptop ini"
RESOLVED=$(dig +short "$DOMAIN" | tail -1)
if [ "$RESOLVED" != "103.242.10.121" ]; then
  echo "DNS belum mengarah dengan benar (dapat: '$RESOLVED', harap: 103.242.10.121). Berhenti."
  exit 1
fi
echo "    OK: $DOMAIN -> $RESOLVED"

echo "==> Baca kredensial Google dari .env.local lokal"
GID=$(grep -E '^GOOGLE_CLIENT_ID=' .env.local | cut -d= -f2-)
GSEC=$(grep -E '^GOOGLE_CLIENT_SECRET=' .env.local | cut -d= -f2-)
[ -n "$GID" ] && [ -n "$GSEC" ] || { echo "GOOGLE_CLIENT_ID/SECRET kosong di .env.local"; exit 1; }

echo "==> Perbarui deploy/.env di VPS (DOMAIN, PUBLIC_URL, GOOGLE_*)"
ssh -o BatchMode=yes -i "$KEY" "$VPS" bash -s -- "$DOMAIN" "$GID" "$GSEC" <<'REMOTE'
set -e
cd /root/ljk
python3 - "$1" "$2" "$3" <<'PY'
import sys, re
domain, gid, gsec = sys.argv[1:4]
p = "deploy/.env"
s = open(p).read()
s = re.sub(r'^DOMAIN=.*$', f'DOMAIN={domain}', s, flags=re.M)
s = re.sub(r'^PUBLIC_URL=.*$', f'PUBLIC_URL=https://{domain}', s, flags=re.M)
s = re.sub(r'^GOOGLE_CLIENT_ID=.*$', f'GOOGLE_CLIENT_ID={gid}', s, flags=re.M)
s = re.sub(r'^GOOGLE_CLIENT_SECRET=.*$', f'GOOGLE_CLIENT_SECRET={gsec}', s, flags=re.M)
open(p, "w").write(s)
PY
echo "--- deploy/.env (rahasia disamarkan) ---"
sed -E 's/(PASSWORD|SECRET|TOKEN|CLIENT_ID)=(.+)/\1=<terisi>/' deploy/.env
REMOTE

echo "==> Restart (Caddy akan otomatis minta sertifikat HTTPS)"
ssh -o BatchMode=yes -i "$KEY" "$VPS" "cd /root/ljk && docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d"

echo "==> Menunggu sertifikat HTTPS terbit (maks 90 detik)"
for i in $(seq 1 18); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "https://$DOMAIN/api/health" || echo 000)
  [ "$CODE" = "200" ] && { echo "    HTTPS aktif! https://$DOMAIN sudah live."; break; }
  sleep 5
done

echo "==> Verifikasi akhir"
curl -s -m 8 "https://$DOMAIN/api/health"; echo
curl -s -m 8 "https://$DOMAIN/auth/me"; echo

cat <<MSG

SELESAI. Langkah manual yang masih perlu Anda cek:
  1) Buka https://$DOMAIN/admin -> klik "Masuk dengan Google" -> pastikan berhasil masuk.
  2) Kalau GAGAL redirect_uri_mismatch: pastikan sudah menambahkan
     https://$DOMAIN/auth/google/callback di Google Cloud Console (Authorized redirect URIs).
  3) Login token lama (ADMIN_TOKEN) otomatis MATI mulai sekarang -> hanya Google yang bisa masuk admin.
MSG
