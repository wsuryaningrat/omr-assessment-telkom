"""Jalankan tes server terhadap PostgreSQL SUNGGUHAN (tertanam via `pgserver`, tanpa Docker).

    .venv-server/bin/python tools/test_postgres.py            # semua tes server
    .venv-server/bin/python tools/test_postgres.py -k regrade # hanya yang cocok

Memastikan skema, migrasi, kolom JSON, urutan kolom rekap, tipe waktu & kueri berjalan di PostgreSQL seperti di VPS.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main():
    import pgserver
    data = tempfile.mkdtemp(prefix="pg-ljk-")
    srv = pgserver.get_server(data)
    try:
        uri = srv.get_uri()   # postgresql://postgres:@/postgres?host=/tmp/...
        url = uri.replace("postgresql://", "postgresql+psycopg://", 1)
        os.environ["TEST_DATABASE_URL"] = os.environ["DATABASE_URL"] = url     # sebelum modul server apa pun diimpor
        print("PostgreSQL:", srv.psql("select version();").strip().splitlines()[-2].strip()[:60])
        from server.db import engine
        assert engine.dialect.name == "postgresql", f"tes berjalan di {engine.dialect.name}, bukan PostgreSQL"
        print("dialek engine:", engine.dialect.name)
        suite = unittest.defaultTestLoader.discover("tests/server", pattern="test_*.py", top_level_dir=".")
        args = [a for a in sys.argv[1:] if not a.startswith("-")]
        if "-k" in sys.argv:
            pat = sys.argv[sys.argv.index("-k") + 1]
            def flat(s):
                for t in s:
                    yield from flat(t) if isinstance(t, unittest.TestSuite) else [t]
            suite = unittest.TestSuite(t for t in flat(suite) if pat in t.id())
        res = unittest.TextTestRunner(verbosity=1).run(suite)
        return 0 if res.wasSuccessful() else 1
    finally:
        srv.cleanup()


if __name__ == "__main__":
    sys.exit(main())
