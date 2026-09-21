# Panduan Deploy ke VPS (IDCloudHost, Ubuntu)

Arsitektur: **Caddy** (HTTPS otomatis) → **API FastAPI** (pemindai di process pool) → **PostgreSQL**, semua dalam Docker Compose.
Target: 1 VPS **2 vCPU / 2 GB RAM / 20 GB disk**. Data utama di PostgreSQL; Google Sheet hanya salinan rekap.

> Status pengujian: kode dan seluruh tes server sudah lolos di **PostgreSQL 16** (tanpa Docker). Berkas `Dockerfile`/`docker-compose.yml`
> **belum pernah dibangun/dijalankan** karena Docker tidak ada di mesin pengembang — **jalankan uji beban (langkah 9) sebelum hari ujian.**

## 0. Yang harus disiapkan dulu
| Kebutuhan | Keterangan |
|---|---|
| VPS Ubuntu 22.04/24.04 | 2 vCPU, 2 GB RAM, ≥20 GB disk, IP publik, akses SSH |
| **Domain** | Wajib untuk HTTPS & login Google. Buat **A record** ke IP VPS (mis. `ljk.namadomain.id → 103.x.x.x`). Domain murah (.my.id/.web.id) cukup |
| Google OAuth | Redirect URI produksi ditambahkan (langkah 6) |
| Google Sheet | Kunci service account **baru** (kunci lama pernah terlihat di percakapan → putar/rotasi dulu) |

## 1. Pesan VPS
Pilih image **Ubuntu 22.04 atau 24.04 LTS**, spek di atas. Catat **IP publik** dan cara login SSH (root atau user + sudo).
Bila panel penyedia punya *firewall/security group*, izinkan masuk hanya TCP **22, 80, 443** (dan UDP 443).

## 2. Arahkan domain
Buat A record `DOMAIN → IP VPS`. Cek propagasi: `dig +short ljk.namadomain.id` harus menampilkan IP VPS. **Tunggu sampai benar sebelum langkah 5** (Caddy gagal memperoleh sertifikat bila DNS belum menunjuk).

## 3. Persiapan server (sekali)
```bash
ssh root@IP_VPS
apt-get update && apt-get install -y git
git clone https://github.com/wsuryaningrat/omr-assessment-telkom.git ljk && cd ljk
git checkout feat/tanpa-streamlit
sudo bash deploy/setup-vps.sh
```
Skrip mengatur: zona waktu WIB, Docker, swap 2 GB, firewall (22/80/443), fail2ban, update keamanan otomatis. **Sebelum menutup sesi SSH**, uji login SSH baru dari terminal lain.

## 4. Konfigurasi
```bash
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env
openssl rand -base64 32     # jalankan 2×: satu untuk POSTGRES_PASSWORD, satu untuk SESSION_SECRET
nano deploy/.env            # isi DOMAIN, PUBLIC_URL, rahasia, ADMIN_EMAILS, GOOGLE_*, GSHEET_URL
```
Kredensial Google Sheet (dari laptop Anda, jangan lewat git):
```bash
scp .streamlit/secrets.toml root@IP_VPS:/root/ljk/deploy/secrets/gsheet.toml
ssh root@IP_VPS "chown 10001 /root/ljk/deploy/secrets/gsheet.toml && chmod 400 /root/ljk/deploy/secrets/gsheet.toml"
```
(`10001` = pengguna non-root di dalam kontainer.) `GSHEET_CREDENTIALS=/run/secrets/gsheet.toml` sudah tertulis di templat.

## 5. Jalankan
```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps      # semua harus "healthy"/"running"
curl -s https://DOMAIN/api/health                                           # {"ok":true,...}
```
Build pertama ±3–6 menit. Bila `ps` menampilkan api `unhealthy`: `docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs api`.

## 6. Login admin Google (produksi)
Di Google Cloud Console → *Credentials* → OAuth client yang sama → **Authorized redirect URIs**, **tambahkan**:
`https://DOMAIN/auth/google/callback`. Pastikan Gmail admin ada di *Test users*. Lalu buka `https://DOMAIN/admin` → **Masuk dengan Google**.
Tanpa login Google/Microsoft terisi, admin memakai `ADMIN_TOKEN` (isi di `.env`).

## 7. Isi kunci jawaban
`/admin` → **Kunci jawaban** → *Tarik dari Google Sheet* (atau unggah `.xlsx`/`.csv`). Tab kunci di Sheet dikenali otomatis.

## 8. Cadangan otomatis
```bash
(crontab -l 2>/dev/null; echo '0 * * * * cd /root/ljk && BACKUP_DIR=/var/backups/ljk KEEP_DAYS=7 bash deploy/backup.sh >> /var/log/ljk-backup.log 2>&1') | crontab -
bash deploy/backup.sh        # uji sekali sekarang
```
Setiap jam, disimpan 7 hari, dan **divalidasi** (gzip utuh + memuat tabel utama). Salin berkala ke luar VPS (mis. `scp`/rclone) — cadangan di server yang sama tidak melindungi dari kerusakan server.
Pulihkan: `bash deploy/restore.sh /var/backups/ljk/ljk-YYYYMMDD-HHMMSS.sql.gz`.

## 9. UJI BEBAN (wajib sebelum hari ujian)
Dari laptop, dengan **foto LJK asli**:
```bash
.venv-server/bin/python tools/loadtest.py --url https://DOMAIN --users 35 --files 40 --photo foto_ljk.jpg
```
Sambil berjalan, di VPS: `bash deploy/status.sh` (pantau RAM/CPU). Tolok ukur: tidak ada berkas gagal; RAM API < 1,3 GB; waktu total dapat diterima.
Bila RAM mepet atau terlalu lambat: naikkan spek VPS (4 vCPU / 4 GB) dan `SCAN_WORKERS`; tidak perlu ubah kode.
**Setelah uji, hapus data uji:** admin → *Sesi*, atau `docker compose ... exec db psql -U omr -d omr -c "TRUNCATE sheet, upload_file, scan_session CASCADE;"`.
Bila Sheet aktif saat uji beban, set `SYNC_SINCE` ke waktu setelah uji supaya baris uji tidak masuk ke spreadsheet produksi.

## 10. Operasi harian
| Perlu | Perintah |
|---|---|
| Lihat kondisi | `bash deploy/status.sh` |
| Log API | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f --tail 100 api` |
| Update versi | `bash deploy/update.sh` (backup → pull → build → tunggu sehat; ada petunjuk rollback bila gagal) |
| Restart | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env restart api` |
| Ekspor rekap | `/admin` → Ekspor (Excel/CSV) |

## 11. Checklist hari-H
- [ ] `status.sh` sehat; disk bebas > 5 GB (`df -h /`); swap aktif (`free -h`)
- [ ] Cadangan terbaru < 1 jam; salinan di luar VPS
- [ ] Kunci jawaban ada untuk semua kode soal (admin → Kunci jawaban)
- [ ] Google Sheet: status "Terhubung", **0 sesi gagal sinkron** (admin → Ringkasan)
- [ ] Uji 1 sesi penuh dari HP asli lewat jaringan seluler (bukan WiFi kampus) → data masuk di Sheet
- [ ] Data uji sudah dihapus dari database dan dari Sheet
- [ ] Domain & sertifikat HTTPS valid; token/kunci rahasia tidak berada di git

## 12. Pemecahan masalah
| Gejala | Kemungkinan penyebab |
|---|---|
| Caddy tidak dapat sertifikat | DNS belum menunjuk ke IP; port 80/443 tertutup (firewall/panel penyedia) |
| `redirect_uri_mismatch` (Google) | URI di Google Cloud tidak persis `https://DOMAIN/auth/google/callback`, atau `PUBLIC_URL` salah |
| Login Google "Akun tidak terdaftar" | Email belum di `ADMIN_EMAILS`; belum jadi *Test user* |
| Upload lambat/gagal untuk banyak pengawas | RAM/CPU kurang → cek `status.sh`; naikkan spek; batasi upload paralel |
| Sheet tidak terisi | Service account belum dibagikan sebagai *Editor*; cek `/admin` → Ringkasan → error terakhir |
| `api` unhealthy setelah update | `logs api`; rollback: `git checkout <commit-lama> && docker compose ... up -d --build` |

## 13. Batasan yang diketahui
- Halaman pengawas **tanpa login** (sengaja, agar mudah); siapa pun yang tahu URL dapat membuat sesi. Ada batas 300 berkas/sesi dan berkas maks. 10 MB. Bila perlu, tambahkan *kode akses ujian* (belum ada).
- Satu VPS = satu titik gagal. Cadangan berkala + salinan luar VPS adalah pengamannya.
- Progres pemindaian PDF multi-halaman baru terlihat setelah seluruh berkas selesai.
