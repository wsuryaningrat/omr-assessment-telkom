import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/omr.db")
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./data/uploads")
# Jumlah proses pemindai. Aturan praktis: jumlah core - 1 (VPS 2 CPU -> 1-2).
SCAN_WORKERS = int(os.environ.get("SCAN_WORKERS", "2"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"}
# Berkas sumber dihapus setelah N jam (0 = hapus segera setelah sesi disubmit/dihapus tidak dilakukan otomatis).
UPLOAD_RETENTION_HOURS = int(os.environ.get("UPLOAD_RETENTION_HOURS", "24"))
