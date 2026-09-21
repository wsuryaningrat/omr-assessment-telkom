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

# ---- Login admin dengan Microsoft (Entra ID). Tanpa MS_* terisi, admin memakai ADMIN_TOKEN (header).
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "")
MS_TENANT_ID = os.environ.get("MS_TENANT_ID", "")           # GUID "Directory (tenant) ID" — JANGAN "common"
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET", "")
ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]      # daftar akun yang boleh
ADMIN_DOMAINS = [d.strip().lower().lstrip("@") for d in os.environ.get("ADMIN_DOMAINS", "").split(",") if d.strip()]
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")     # mis. https://ljk.kampus.ac.id (wajib di belakang proxy)
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")         # kosong = acak per proses (sesi hilang saat restart)
ADMIN_SESSION_HOURS = int(os.environ.get("ADMIN_SESSION_HOURS", "8"))
ADMIN_ALLOW_TOKEN = os.environ.get("ADMIN_ALLOW_TOKEN", "0") == "1"   # izinkan ADMIN_TOKEN walau login Microsoft aktif

# ---- Login admin dengan Google (akun Gmail / Google Workspace). Bisa aktif bersamaan dengan Microsoft.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
