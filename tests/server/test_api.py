"""Uji integrasi API: alur lengkap upload -> pindai -> validasi -> submit -> ekspor.

Jalankan (pakai venv server):  .venv-server/bin/python -m unittest tests.server.test_api
Hasil pemindaian lewat API harus sama dengan golden regresi (jalur PDF).
"""
import json
import os
import tempfile
import time
import unittest

_tmp = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{_tmp}/t.db", UPLOAD_DIR=f"{_tmp}/up",
                  SCAN_WORKERS="2", ADMIN_TOKEN="rahasia", MAX_UPLOAD_MB="10")

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from tests.regression.fixtures import KUNCI  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GOLDEN = json.load(open(os.path.join(ROOT, "tests", "regression", "golden_scan.json"), encoding="utf-8"))
VALID = {"nama_pengawas": "Budi Santoso", "hp": "081234567890", "ruangan": "TULT 0603",
         "fakultas": "FIF - Fakultas Informatika", "prodi": "S1 Informatika"}


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = TestClient(app)
        cls.c = cls._ctx.__enter__()
        cls.c.put("/api/admin/kunci/A", json={str(q): a for q, a in KUNCI["A"].items()}, headers={"X-Admin-Token": "rahasia"})

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def wait(self, sid, timeout=120):
        t = time.time()
        while time.time() - t < timeout:
            d = self.c.get(f"/api/sessions/{sid}").json()
            if not d["scanning"]:
                return d
            time.sleep(0.5)
        self.fail("pemindaian tidak selesai")

    def test_validation_rules(self):
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "hp": "12"}).status_code, 422)
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "prodi": "S1 Film"}).status_code, 422)
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "ruangan": " "}).status_code, 422)
        self.assertEqual(self.c.get("/api/meta").json()["fakultas_prodi"].keys(), __import__("scanner.service", fromlist=["x"]).FAKULTAS_PRODI.keys())

    def test_ui_is_served(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Evaluasi LJK", r.text)
        self.assertEqual(self.c.get("/app.js").status_code, 200)
        self.assertEqual(self.c.get("/api/health").json()["ok"], True)

    def test_upload_rejects_bad_type_and_admin_needs_token(self):
        sid = self.c.post("/api/sessions", json=VALID).json()["id"]
        r = self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
        self.assertEqual(r.status_code, 415)
        self.assertEqual(self.c.get("/api/admin/export.csv").status_code, 401)

    def test_full_flow_matches_golden(self):
        sid = self.c.post("/api/sessions", json=VALID).json()["id"]
        with open(os.path.join(ROOT, "LJK.pdf"), "rb") as f:
            r = self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("LJK.pdf", f.read(), "application/pdf"))])
        self.assertEqual(r.status_code, 202)
        d = self.wait(sid)
        self.assertEqual(d["summary"]["lembar"], 1)
        self.assertEqual(d["sheets"][0]["label"], "Perlu Validasi")

        # submit sebelum validasi harus ditolak
        self.assertEqual(self.c.post(f"/api/sessions/{sid}/submit").status_code, 409)

        # preview on-demand: satu JPEG
        shid = d["sheets"][0]["id"]
        pv = self.c.get(f"/api/sheets/{shid}/preview")
        self.assertEqual((pv.status_code, pv.headers["content-type"]), (200, "image/jpeg"))

        self.assertEqual(self.c.post(f"/api/sessions/{sid}/validate-all").status_code, 200)
        self.assertEqual(self.c.post(f"/api/sessions/{sid}/submit").json()["lembar"], 1)
        self.assertEqual(self.c.post(f"/api/sheets/{shid}/unvalidate").status_code, 409)  # sudah disubmit

        csv_text = self.c.get("/api/admin/export.csv", headers={"X-Admin-Token": "rahasia"}).text
        gold = GOLDEN["blank_pdf"]["record"]
        for col in ("Nilai", "Jawaban Terisi", "Jumlah Benar", "Kode Soal", "NPM", "Nama Pengawas", "Ruangan", "Program Studi"):
            self.assertIn(col, csv_text.splitlines()[0])
        # hasil baca identik dengan jalur Streamlit (golden)
        import csv, io
        row = next(csv.DictReader(io.StringIO(csv_text)))
        for k in ("Jawaban Terisi", "Nilai", "Jumlah Benar", "Jumlah Salah", "Jumlah Kosong", "NPM", "Kode Soal") + tuple(x for x in gold if x.startswith("soal_")):
            self.assertEqual(row[k], str(gold[k]), k)

    def test_many_files_concurrent(self):
        sid = self.c.post("/api/sessions", json=VALID).json()["id"]
        data = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()
        files = [("files", (f"a{i}.pdf", data, "application/pdf")) for i in range(6)]
        self.assertEqual(self.c.post(f"/api/sessions/{sid}/files", files=files).status_code, 202)
        d = self.wait(sid)
        self.assertEqual((d["summary"]["lembar"], d["files"]["failed"]), (6, []))
        one = d["sheets"][0]["id"]
        self.assertEqual(self.c.delete(f"/api/sheets/{one}").status_code, 204)
        self.assertEqual(self.c.get(f"/api/sessions/{sid}").json()["summary"]["lembar"], 5)


if __name__ == "__main__":
    unittest.main()
