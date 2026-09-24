"""Uji integrasi API: alur lengkap upload -> pindai -> validasi -> submit -> ekspor.

Jalankan (pakai venv server):  .venv-server/bin/python -m unittest tests.server.test_api
Hasil pemindaian lewat API harus sama dengan golden regresi (jalur PDF).
"""
import json
import os
import tempfile
import time
import unittest

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from tests.regression.fixtures import KUNCI  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GOLDEN = json.load(open(os.path.join(ROOT, "tests", "regression", "golden_scan.json"), encoding="utf-8"))
VALID = {"nama_pengawas": "Budi Santoso", "hp": "081234567890", "ruangan": "TULT 0603", "kelas": "BS1SI-50-REG-01",
         "prodi": "S1 Sistem Informasi"}


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
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "hp": "0712345678"}).status_code, 422)   # bukan seluler (harus 8…)
        for ok in ("081234567890", "+62 812-3456-7890", "6281234567890", "81234567890"):
            r = self.c.post("/api/sessions", json={**VALID, "hp": ok})
            self.assertEqual(r.status_code, 201, ok)
            self.assertEqual(self.c.get(f"/api/sessions/{r.json()['id']}").json()["pengawas"]["hp"], "+6281234567890", ok)
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "prodi": " "}).status_code, 422)
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "kelas": " "}).status_code, 422)
        # isi sendiri: prodi/kelas di luar daftar atau gabungan diterima apa adanya
        r = self.c.post("/api/sessions", json={**VALID, "prodi": "S1 Film, S1 Kriya", "kelas": "GAB-XYZ-01, GAB-XYZ-02"})
        self.assertEqual(r.status_code, 201)
        d = self.c.get(f"/api/sessions/{r.json()['id']}").json()["pengawas"]
        self.assertEqual((d["prodi"], d["kelas"]), ("S1 Film, S1 Kriya", "GAB-XYZ-01, GAB-XYZ-02"))
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "prodi": "x" * 151}).status_code, 422)
        m = self.c.get("/api/meta").json()
        self.assertGreater(len(m["kelas"]), 100)
        self.assertEqual(m["prodi"], sorted(set(k["prodi"] for k in m["kelas"]), key=str.lower))
        self.assertNotIn("fakultas_prodi", m)

    def test_pengawas_dropdown_and_registered_phone(self):
        import tempfile
        from server import refdata
        f = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8")
        f.write("Nama Pengawas\tNIM\tNo HP\nBudi Z\t1001\t6281111111111\nAni A\t1002\t82222222222\nDra. Dosen\t\t\nCici Tanpa HP\t1003\t621220867079\n")
        f.close()
        old = refdata.PENGAWAS_FILE
        refdata.PENGAWAS_FILE = f.name
        refdata.pengawas.cache_clear()
        try:
            p = self.c.get("/api/meta").json()["pengawas"]
            self.assertEqual([x["nama"] for x in p], ["Ani A", "Budi Z", "Cici Tanpa HP", "Dra. Dosen"])
            self.assertTrue(p[3]["dosen"] and p[3]["needs_hp"] and p[2]["needs_hp"] and not p[0]["needs_hp"])
            self.assertNotIn("hp", p[0])
            base = {"kelas": VALID["kelas"], "prodi": VALID["prodi"]}
            r = self.c.post("/api/sessions", json={**base, "pengawas_ref": p[1]["id"]})
            self.assertEqual(r.status_code, 201)
            d = self.c.get(f"/api/sessions/{r.json()['id']}").json()["pengawas"]
            self.assertEqual((d["nama"], d["hp"]), ("Budi Z", "+6281111111111"))
            self.assertEqual(self.c.post("/api/sessions", json={**base, "pengawas_ref": p[3]["id"]}).status_code, 422)
            r = self.c.post("/api/sessions", json={**base, "pengawas_ref": p[3]["id"], "hp": "0812345678901"})
            self.assertEqual(r.status_code, 201)
            self.assertEqual(self.c.post("/api/sessions", json={**base, "pengawas_ref": "zzz"}).status_code, 422)
        finally:
            refdata.PENGAWAS_FILE = old
            refdata.pengawas.cache_clear()
            os.unlink(f.name)

    def test_ui_is_served(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Literasi Numerik", r.text)
        r = self.c.get("/ljk")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Evaluasi LJK", r.text)
        self.assertIn("ljk()", r.text)
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

    def test_edit_identity_updates_sheets_and_export(self):
        sid = self.c.post("/api/sessions", json=VALID).json()["id"]
        data = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("LJK.pdf", data, "application/pdf"))])
        self.wait(sid)
        new = {**VALID, "nama_pengawas": "Siti Aminah", "kelas": "BS1TI-50-REG-01",
               "prodi": "S1 Teknik Industri"}
        self.assertEqual(self.c.patch(f"/api/sessions/{sid}", json=new).status_code, 200)
        d = self.c.get(f"/api/sessions/{sid}").json()
        self.assertEqual((d["pengawas"]["nama"], d["pengawas"]["kelas"]), ("Siti Aminah", "BS1TI-50-REG-01"))
        self.assertEqual(self.c.patch(f"/api/sessions/{sid}", json={**new, "kelas": ""}).status_code, 422)
        self.c.post(f"/api/sessions/{sid}/validate-all")
        self.c.post(f"/api/sessions/{sid}/submit")
        import csv, io
        rows = list(csv.DictReader(io.StringIO(self.c.get("/api/admin/export.csv", headers={"X-Admin-Token": "rahasia"}).text)))
        mine = [r for r in rows if r["Nama Pengawas"] == "Siti Aminah"]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["Kelas"], mine[0]["Ruangan"], mine[0]["Program Studi"]),
                         ("BS1TI-50-REG-01", "-", "S1 Teknik Industri"))
        self.assertEqual(mine[0]["Fakultas"], mine[0]["Fakultas (LJK)"])
        self.assertEqual(list(rows[0].keys())[:5], ["Submit Date", "Nama Pengawas", "No HP Pengawas", "Ruangan", "Kelas"])
        self.assertEqual(self.c.patch(f"/api/sessions/{sid}", json=new).status_code, 409)  # sudah disubmit

    def test_validate_batch_only_given_ids(self):
        sid = self.c.post("/api/sessions", json=VALID).json()["id"]
        data = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", (f"b{i}.pdf", data, "application/pdf")) for i in range(4)])
        d = self.wait(sid)
        ids = [x["id"] for x in d["sheets"]]
        r = self.c.post("/api/sheets/validate-batch", json={"ids": ids[:2], "value": True}).json()
        self.assertEqual(r["changed"], 2)
        d = self.c.get(f"/api/sessions/{sid}").json()
        self.assertEqual((d["summary"]["ok"], d["summary"]["perlu_validasi"]), (2, 2))
        self.assertEqual(self.c.post("/api/sheets/validate-batch", json={"ids": ids[:2], "value": False}).json()["changed"], 2)
        self.assertEqual(self.c.get(f"/api/sessions/{sid}").json()["summary"]["ok"], 0)
        self.c.post(f"/api/sessions/{sid}/validate-all")
        self.c.post(f"/api/sessions/{sid}/submit")
        self.assertIsNotNone(self.c.get(f"/api/sessions/{sid}").json()["submitted_at"])
        self.assertEqual(self.c.post("/api/sheets/validate-batch", json={"ids": ids[:1]}).status_code, 409)

    def test_file_cap_per_session(self):
        from server import config
        old = config.MAX_FILES_PER_SESSION
        config.MAX_FILES_PER_SESSION = 2
        try:
            sid = self.c.post("/api/sessions", json=VALID).json()["id"]
            data = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()
            two = [("files", (f"c{i}.pdf", data, "application/pdf")) for i in range(2)]
            self.assertEqual(self.c.post(f"/api/sessions/{sid}/files", files=two).status_code, 202)
            r = self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("c9.pdf", data, "application/pdf"))])
            self.assertEqual(r.status_code, 413)                  # sudah 2 -> berkas ke-3 ditolak
        finally:
            config.MAX_FILES_PER_SESSION = old

    def test_clientlog_is_rate_limited(self):
        from server import config, main
        main._CLOG.clear()
        old = config.CLIENTLOG_PER_MIN
        config.CLIENTLOG_PER_MIN = 3
        try:
            with self.assertLogs("uvicorn.error", level="INFO") as cm:
                for i in range(8):
                    self.assertEqual(self.c.post("/api/clientlog", json={"ev": "t", "i": i}).status_code, 204)
            logged = [l for l in cm.output if "CLIENT" in l]
            self.assertEqual(len(logged), 3)                     # sisanya diabaikan diam-diam
        finally:
            config.CLIENTLOG_PER_MIN = old
            main._CLOG.clear()

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
