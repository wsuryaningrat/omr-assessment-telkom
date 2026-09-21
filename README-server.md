# LJK Scanner

Pemindai LJK (Lembar Jawaban Komputer) Profiling Literasi Numerik — Telkom University.
Versi tanpa Streamlit: backend FastAPI + antrian pemindai (process pool) + database SQL.

Logika pemindaian (`core/`, `scanner/`) sama dengan versi Streamlit; hasilnya dijaga oleh
`tests/regression` (golden output). **Versi OpenCV dikunci** (`opencv-python-headless==5.0.0.93`)
karena versi lain terbukti mengubah hasil baca.

## Jalankan lokal
```bash
./run-server.sh            # membuat .venv-server, memasang dependensi, menjalankan di http://localhost:8000
```
Database lokal = SQLite (`data/omr.db`), tidak perlu instal apa pun. Dokumentasi API: `/docs`.

## Tes
```bash
.venv-server/bin/python -m unittest tests.regression.test_scan_regression tests.server.test_api tests.server.test_services
```

## Uji beban (VPS)
```bash
.venv-server/bin/python tools/loadtest.py --url http://IP:8000 --users 35 --files 40 --photo foto_ljk.jpg
```

## Deploy (VPS)
`deploy/docker-compose.yml` (Postgres + API + Caddy). Isi `deploy/.env` dengan `POSTGRES_PASSWORD` dan `ADMIN_TOKEN`, lalu:
```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

## Konfigurasi (env)
| Variabel | Default | Keterangan |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/omr.db` | Produksi: `postgresql+psycopg://...` |
| `UPLOAD_DIR` | `./data/uploads` | Berkas sumber sementara |
| `SCAN_WORKERS` | `2` | Jumlah proses pemindai (≈ jumlah core) |
| `MAX_UPLOAD_MB` | `10` | Batas per berkas |
| `ADMIN_TOKEN` | – | Header `X-Admin-Token` untuk endpoint admin |
| `SCAN_MAX_SIDE` | kosong | Batas sisi gambar saat decode. Kosong = resolusi asli (akurasi identik) |

## Halaman admin
Buka `/admin` dan masuk dengan `ADMIN_TOKEN`. Tab: **Ringkasan** (status sinkron, tombol sinkron/tarik kunci/bersihkan), **Sesi** (semua sesi pengawas), **Kunci jawaban** (unggah `.xlsx`/`.csv`, tarik dari Google Sheet, hapus, **hitung ulang nilai** semua sesi yang sudah disubmit), **Ekspor** (Excel/CSV, filter kelas).

## Google Sheet (opsional)
Tanpa konfigurasi, data tetap aman di database dan bisa diekspor. Untuk sinkron otomatis:

1. Buat *service account* di Google Cloud, aktifkan **Google Sheets API**, unduh kunci JSON.
2. Bagikan spreadsheet ke email service account (peran *Editor*).
3. Atur env: `GSHEET_URL` (URL spreadsheet), `GSHEET_CREDENTIALS` (path berkas JSON atau isi JSON-nya), `GSHEET_WORKSHEET` (tab rekap, default `Sheet1`).

Cara kerja: saat pengawas submit, hasil dinilai ulang dengan kunci terbaru lalu masuk antrean (outbox). Latar belakang mengirim semua sesi yang jatuh tempo dalam **satu permintaan batch** tiap `SYNC_INTERVAL_S` detik (hemat kuota API). Gagal → dicoba lagi dengan jeda bertambah (maks. `SYNC_MAX_ATTEMPTS`); sesi bermasalah tidak menahan sesi lain. Kolom baru otomatis ditambahkan ke header. Kunci jawaban (tab selain rekap) ditarik tiap `KUNCI_SYNC_INTERVAL_S` detik.

| Env | Default | Keterangan |
|---|---|---|
| `SYNC_INTERVAL_S` | 10 | Jeda pemeriksaan antrean sinkron |
| `SYNC_MAX_ATTEMPTS` | 10 | Percobaan maksimum sebelum menunggu tindakan admin |
| `KUNCI_SYNC_INTERVAL_S` | 600 | Jeda tarik kunci dari Sheet |
| `UPLOAD_RETENTION_HOURS` | 24 | Berkas sumber sesi yang sudah submit dihapus setelah ini |
| `UNSUBMITTED_RETENTION_HOURS` | 72 | Idem untuk sesi yang tidak pernah disubmit |
| `CLEANUP_INTERVAL_S` | 1800 | Jeda pembersihan |
| `BACKGROUND_TASKS` | 1 | Set 0 untuk mematikan tugas latar (dipakai tes) |
