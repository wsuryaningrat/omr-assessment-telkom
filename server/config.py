import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/omr.db")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./data/uploads")
# Jumlah proses pemindai. Aturan praktis: jumlah core - 1 (VPS 2 CPU -> 1-2).
SCAN_WORKERS = int(os.environ.get("SCAN_WORKERS", "2"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"}
# Berkas sumber dihapus setelah N jam (0 = hapus segera setelah sesi disubmit/dihapus tidak dilakukan otomatis).
UPLOAD_RETENTION_HOURS = int(os.environ.get("UPLOAD_RETENTION_HOURS", "24"))

# ---- Google Sheet (rekap + kunci jawaban). Tanpa GSHEET_URL fitur sinkron nonaktif; data tetap aman di database.
GSHEET_URL = os.environ.get("GSHEET_URL", "")
GSHEET_CREDENTIALS = os.environ.get("GSHEET_CREDENTIALS", "")   # path berkas JSON service account, atau isi JSON-nya
GSHEET_WORKSHEET = os.environ.get("GSHEET_WORKSHEET", "Sheet1")  # tab rekap hasil
SYNC_INTERVAL_S = int(os.environ.get("SYNC_INTERVAL_S", "10"))
SYNC_MAX_ATTEMPTS = int(os.environ.get("SYNC_MAX_ATTEMPTS", "10"))
KUNCI_SYNC_INTERVAL_S = int(os.environ.get("KUNCI_SYNC_INTERVAL_S", "600"))
CLEANUP_INTERVAL_S = int(os.environ.get("CLEANUP_INTERVAL_S", "1800"))
UNSUBMITTED_RETENTION_HOURS = int(os.environ.get("UNSUBMITTED_RETENTION_HOURS", "72"))
# Hanya sinkronkan sesi yang disubmit pada/setelah waktu ini (ISO 8601, mis. 2026-09-22T00:00:00Z). Mencegah data uji lama ikut terkirim.
SYNC_SINCE = os.environ.get("SYNC_SINCE", "")
