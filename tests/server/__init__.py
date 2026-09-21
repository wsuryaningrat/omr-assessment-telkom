"""Lingkungan uji server: DB SQLite sementara, tanpa tugas latar (diuji langsung), token admin tetap."""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update(
    DATABASE_URL=f"sqlite:///{_tmp}/t.db", UPLOAD_DIR=f"{_tmp}/up", SCAN_WORKERS="2",
    ADMIN_TOKEN="rahasia", MAX_UPLOAD_MB="10", BACKGROUND_TASKS="0",
    GSHEET_URL="", GSHEET_CREDENTIALS="",
)
