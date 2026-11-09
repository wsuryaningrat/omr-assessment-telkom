# Integrasi LJK Scanner v2 ke mathcenter.cswda.id

Situs utama sudah punya menu **Evaluasi LJK → `/ljk`** (versi lama, upload massal).
Aplikasi v2 bisa dipasang dengan salah satu cara di bawah. Tampilan v2 sudah memakai
palet, font, dan logo yang sama dengan situs utama (Math Center, Telkom University, CSWDA).

## Dipakai: `mathcenter.cswda.id/ljk` (menggantikan v1) lewat Caddy di VPS Math Center

Situs utama (FastAPI + Caddy, `deploy/` di branch `deploy/no-cnn`) sudah disiapkan di branch
**`feat/integrasi-v2`**: Caddy meneruskan `/ljk` dan `/ljk/*` ke Vercel, sehingga halaman
upload v1 di `/ljk` diganti v2. `/admin`, `/panduan`, `/monitor`, dan `/api/*` tetap dilayani FastAPI.
Menu "Evaluasi LJK" dan tombol di beranda tetap ke `/ljk` (deskripsinya sudah disesuaikan).

Urutan go-live:

1. **Vercel** → `ljk-scanner-v2` → Settings → Environment Variables (Production):
   `NEXT_PUBLIC_BASE_PATH=/ljk`, `NEXT_PUBLIC_SITE_URL=https://mathcenter.cswda.id` → Redeploy.
   (Alamat vercel.app ikut pindah ke `https://ljk-scanner-v2.vercel.app/ljk`.)
   `NEXT_PUBLIC_SITE_URL` wajib: Vercel menimpa `x-forwarded-host`, jadi redirect login
   memakai env ini agar pengguna tetap di mathcenter.cswda.id.
2. **Supabase** → Authentication → URL Configuration: Site URL `https://mathcenter.cswda.id/ljk`,
   tambah Redirect URL `https://mathcenter.cswda.id/ljk/auth/callback`.
3. **Google Auth Platform** → Branding: Authorized domains tambah `cswda.id`; home page
   `https://mathcenter.cswda.id/ljk`, privasi `…/ljk/privasi`, ketentuan `…/ljk/ketentuan`.
4. **VPS**: merge `feat/integrasi-v2` ke branch yang dipakai VPS, lalu `bash deploy/update.sh`.
   Cek Caddyfile dulu: `docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec caddy caddy validate --config /etc/caddy/Caddyfile`.
   Rollback cukup `git checkout <commit sebelumnya> && bash deploy/update.sh` (v1 kembali di /ljk).
5. Buka `https://mathcenter.cswda.id/ljk` → login Google → scan.

Catatan: template LJK v2 untuk diunduh ada di `https://mathcenter.cswda.id/ljk/ljk/LJK_v2.pdf`
(folder `public/ljk` di aplikasi). Halaman `/panduan` situs utama masih menjelaskan alur upload v1.

## Pilihan A — Subdomain (paling sederhana)

Alternatif bila tidak ingin lewat VPS. Contoh: `ljk.mathcenter.cswda.id`.

1. **Vercel** → project `ljk-scanner-v2` → Settings → Domains → tambah `ljk.mathcenter.cswda.id`.
2. **DNS cswda.id** → tambah record `CNAME ljk.mathcenter → cname.vercel-dns.com`.
3. **Env Vercel**: tidak perlu `NEXT_PUBLIC_BASE_PATH`. Opsional `NEXT_PUBLIC_SITE_URL=https://ljk.mathcenter.cswda.id`.
4. Situs utama: arahkan tombol/menu "Evaluasi LJK" ke `https://ljk.mathcenter.cswda.id`.
5. Lanjut ke bagian **Login Google & Supabase** di bawah.

## Pilihan B — Satu domain, di bawah path (`mathcenter.cswda.id/ljk`)

Server situs utama meneruskan (reverse proxy) semua request `/ljk/*` ke Vercel.

1. **Env Vercel** (Production): `NEXT_PUBLIC_BASE_PATH=/ljk` dan `NEXT_PUBLIC_SITE_URL=https://mathcenter.cswda.id`, lalu redeploy.
   Setelah itu aplikasi melayani `https://ljk-scanner-v2.vercel.app/ljk/...`.
2. **Reverse proxy di server situs utama**, contoh nginx:

   ```nginx
   location /ljk/ {
       proxy_pass https://ljk-scanner-v2.vercel.app/ljk/;
       proxy_set_header Host ljk-scanner-v2.vercel.app;
       proxy_set_header X-Forwarded-Host $host;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_ssl_server_name on;
       proxy_buffering off;
       client_max_body_size 15m;
   }
   location = /ljk { return 301 /ljk/; }
   ```

   Bila situs utama juga di Vercel, cukup `vercel.json` di project situs utama:

   ```json
   { "rewrites": [{ "source": "/ljk/:path*", "destination": "https://ljk-scanner-v2.vercel.app/ljk/:path*" }] }
   ```

3. Halaman `/ljk` lama di situs utama harus dipindah (mis. ke `/ljk-v1`) supaya tidak bentrok.

Aplikasi sudah siap untuk ini: semua tautan, gambar, form logout, dan redirect login memakai
basePath (`lib/basePath.ts`), dan redirect server memakai `x-forwarded-host` / `NEXT_PUBLIC_SITE_URL`
sehingga pengguna tetap di `mathcenter.cswda.id`.

## Login Google & Supabase (wajib untuk A maupun B)

- **Supabase** → Authentication → URL Configuration → Redirect URLs, tambahkan:
  - A: `https://ljk.mathcenter.cswda.id/auth/callback`
  - B: `https://mathcenter.cswda.id/ljk/auth/callback`
  Site URL boleh diganti ke alamat baru.
- **Google Cloud** → Google Auth Platform → Branding → Authorized domains: tambahkan `cswda.id`,
  dan perbarui link home page / privasi / ketentuan ke alamat baru (`…/privasi`, `…/ketentuan`).
  Redirect URI OAuth client **tidak berubah** (tetap ke `…supabase.co/auth/v1/callback`).

## Kenapa bukan iframe?

Halaman login Google menolak dibuka di dalam iframe, dan kamera di iframe butuh `allow="camera"`
dari halaman induk. Jadi v2 sebaiknya dibuka sebagai halaman penuh (A atau B). Header
`Content-Security-Policy: frame-ancestors` tetap mengizinkan `mathcenter.cswda.id` / `*.cswda.id`
(atur lewat env `FRAME_ANCESTORS`) bila suatu saat ingin menyematkan halaman publik seperti panduan.

## Ringkasan env

| Env | Isi | Kapan |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | dari Supabase | selalu |
| `NEXT_PUBLIC_BASE_PATH` | `/ljk` | hanya pilihan B |
| `NEXT_PUBLIC_SITE_URL` | origin publik | disarankan bila di balik proxy |
| `FRAME_ANCESTORS` | daftar origin | opsional |
| `NEXT_PUBLIC_DEMO_MODE` | `1` | hanya untuk uji, jangan di produksi |
