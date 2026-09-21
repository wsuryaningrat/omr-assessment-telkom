#!/usr/bin/env bash
# Persiapan VPS Ubuntu 22.04/24.04 yang masih kosong. Jalankan SEKALI sebagai root:
#   sudo bash deploy/setup-vps.sh
# Aman dijalankan ulang (idempoten). Isi: zona waktu WIB, paket dasar, Docker, swap 2 GB, firewall, fail2ban.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Jalankan sebagai root: sudo bash $0"; exit 1; }
SWAP_GB=${SWAP_GB:-2}

echo "==> Zona waktu & paket dasar"
timedatectl set-timezone Asia/Jakarta || true
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl git ufw fail2ban unattended-upgrades gnupg

echo "==> Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
docker compose version >/dev/null 2>&1 || apt-get install -y docker-compose-plugin

echo "==> Swap ${SWAP_GB} GB (penyangga saat RAM 2 GB penuh)"
if ! swapon --show | grep -q .; then
  fallocate -l "${SWAP_GB}G" /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=$((SWAP_GB*1024))
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -w vm.swappiness=20 >/dev/null
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=20' >> /etc/sysctl.conf

echo "==> Firewall (hanya SSH, HTTP, HTTPS)"
SSH_PORT=$(ss -tlnp 2>/dev/null | awk '/sshd/{n=split($4,a,":"); print a[n]; exit}'); SSH_PORT=${SSH_PORT:-22}
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "${SSH_PORT}/tcp" >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null
ufw --force enable >/dev/null
echo "    SSH terbuka di port ${SSH_PORT} (jangan tutup sesi ini sebelum menguji SSH baru dari terminal lain)."

echo "==> fail2ban (blokir percobaan login SSH berulang) & pembaruan keamanan otomatis"
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades || true

echo
echo "SELESAI. Selanjutnya:"
echo "  1) git clone <repo> && cd <repo> && git checkout feat/tanpa-streamlit"
echo "  2) cp deploy/.env.example deploy/.env && chmod 600 deploy/.env && nano deploy/.env"
echo "  3) docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build"
free -h | sed -n '1,3p'
