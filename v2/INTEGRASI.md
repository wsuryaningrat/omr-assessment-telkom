# Integrasi LJK Scanner v2 ke mathcenter.cswda.id

Situs utama sudah punya menu **Evaluasi LJK → `/ljk`** (versi lama, upload massal).
Aplikasi v2 bisa dipasang dengan salah satu cara di bawah. Tampilan v2 sudah memakai
palet, font, dan logo yang sama dengan situs utama (Math Center, Telkom University, CSWDA).

## Pilihan A — Subdomain (paling sederhana)

Contoh: `ljk.mathcenter.cswda.id` (atau `scan.mathcenter.cswda.id`).

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
