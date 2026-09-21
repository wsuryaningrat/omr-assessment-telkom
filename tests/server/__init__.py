"""Lingkungan uji server: DB SQLite sementara, tanpa tugas latar (diuji langsung), token admin tetap."""
import os
import tempfile

_tmp = tempfile.mkdtemp()
# TEST_DATABASE_URL (mis. dari tools/test_postgres.py) menguji terhadap PostgreSQL; default SQLite sementara.
os.environ.update(
    DATABASE_URL=os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/t.db", UPLOAD_DIR=f"{_tmp}/up", SCAN_WORKERS="2",
    ADMIN_TOKEN="rahasia", MAX_UPLOAD_MB="10", BACKGROUND_TASKS="0",
    GSHEET_URL="", GSHEET_CREDENTIALS="",
)
