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
.venv-server/bin/python -m unittest tests.regression.test_scan_regression tests.server.test_api
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
