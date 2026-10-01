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
         "prodi": "S1 Sistem Informasi", "fakultas": "FIF", "hari_ujian": "2026-09-28", "kode_soal": "A"}


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
        # hari ujian & kode soal (baru): wajib diisi. Hari ujian = tanggal TETAP (28 Sep - 2 Okt 2026), bukan
        # hari-dalam-minggu berulang -- lihat HARI_UJIAN di server/main.py. Kode soal = isian singkat bebas
        # (bukan dicocokkan ke daftar kunci -- lihat _resolve_identity).
        self.assertIn("A", m["kunci"])
        self.assertEqual([h["value"] for h in m["hari"]], ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"])
        self.assertEqual(m["hari"][1]["label"], "Selasa, 29 September 2026")
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "hari_ujian": ""}).status_code, 422)
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "hari_ujian": "2026-09-27"}).status_code, 422)   # di luar 28 Sep-2 Okt
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "kode_soal": ""}).status_code, 422)
        self.assertEqual(self.c.post("/api/sessions", json={**VALID, "kode_soal": "x" * 21}).status_code, 422)   # terlalu panjang
        r = self.c.post("/api/sessions", json={**VALID, "kode_soal": "TIDAK-ADA-DI-KUNCI"})   # bebas, tak perlu cocok kunci
        self.assertEqual(r.status_code, 201, r.text)
        r = self.c.post("/api/sessions", json={**VALID, "hari_ujian": "2026-09-29"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.c.get(f"/api/sessions/{r.json()['id']}").json()["pengawas"]["hari_ujian"], "2026-09-29")

    def test_pending_counter_returns_to_zero(self):
        from server import main as srv
        sid = self.c.post("/api/sessions", json=VALID).json()["id"]
        data = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("LJK.pdf", data, "application/pdf"))])
        self.wait(sid)
        for _ in range(50):
            if srv._pending.value == 0:
                break
            time.sleep(0.1)
        self.assertEqual(srv._pending.value, 0)

    def test_pengawas_dropdown_and_registered_phone(self):
        from server.db import Pengawas, SessionLocal
        ids = ["tes_pw_budiz", "tes_pw_ania", "tes_pw_dosen", "tes_pw_cici", "tes_pw_nonimtanpagelar"]
        with SessionLocal() as db:
            db.add_all([
                Pengawas(id=ids[0], nama="Budi Z", nim="1001", hp="6281111111111"),
                Pengawas(id=ids[1], nama="Ani A", nim="1002", hp="82222222222"),
                Pengawas(id=ids[2], nama="Dra. Dosen", nim="", hp=""),
                Pengawas(id=ids[3], nama="Cici Tanpa HP", nim="1003", hp="621220867079"),
                # Peninggalan migrasi lama/entri "Lainnya -- isi sendiri" (main.py _sync_pengawas_contact):
                # nim="" TAPI org-nya mahasiswa (namanya tanpa gelar) -- nim kosong SENDIRIAN tak boleh
                # dibaca "dosen" (lihat refdata._looks_like_dosen), harus tetap diminta isi HP.
                Pengawas(id=ids[4], nama="Zaki Tanpa Nim Tanpa Gelar", nim="", hp=""),
            ])
            db.commit()
        try:
            p = [x for x in self.c.get("/api/meta").json()["pengawas"] if x["id"] in ids]   # abaikan baris pengawas lain yg mungkin ada
            self.assertEqual([x["nama"] for x in p], ["Ani A", "Budi Z", "Cici Tanpa HP", "Zaki Tanpa Nim Tanpa Gelar", "Dra. Dosen"])
            # dosen: tidak ada isian HP sama sekali; mahasiswa tanpa HP terdaftar (ber-NIM ATAU tak ber-NIM
            # krn blm sempat tercatat) tetap wajib mengisi sendiri -- cuma nim kosong + gelar yg dianggap dosen
            self.assertTrue(p[4]["dosen"] and not p[4]["needs_hp"] and p[2]["needs_hp"] and not p[0]["needs_hp"])
            self.assertTrue(not p[3]["dosen"] and p[3]["needs_hp"])   # "Zaki..." -- nim kosong TANPA gelar
            self.assertNotIn("hp", p[0])
            base = {"kelas": VALID["kelas"], "prodi": VALID["prodi"], "fakultas": VALID["fakultas"], "hari_ujian": VALID["hari_ujian"], "kode_soal": VALID["kode_soal"]}
            r = self.c.post("/api/sessions", json={**base, "pengawas_ref": p[1]["id"]})
            self.assertEqual(r.status_code, 201)
            d = self.c.get(f"/api/sessions/{r.json()['id']}").json()["pengawas"]
            self.assertEqual((d["nama"], d["hp"]), ("Budi Z", "+6281111111111"))
            r = self.c.post("/api/sessions", json={**base, "pengawas_ref": p[4]["id"]})           # dosen tanpa HP: sah
            self.assertEqual(r.status_code, 201)
            d = self.c.get(f"/api/sessions/{r.json()['id']}").json()["pengawas"]
            self.assertEqual((d["nama"], d["hp"]), ("Dra. Dosen", ""))
            # HP kiriman klien diabaikan untuk dosen (UI memang tak menampilkannya)
            r = self.c.post("/api/sessions", json={**base, "pengawas_ref": p[4]["id"], "hp": "0812345678901"})
            self.assertEqual(r.status_code, 201)
            self.assertEqual(self.c.get(f"/api/sessions/{r.json()['id']}").json()["pengawas"]["hp"], "")
            # mahasiswa yang HP-nya tak terdaftar TETAP wajib mengisi
            self.assertEqual(self.c.post("/api/sessions", json={**base, "pengawas_ref": p[2]["id"]}).status_code, 422)
            r = self.c.post("/api/sessions", json={**base, "pengawas_ref": p[2]["id"], "hp": "0812345678901"})
            self.assertEqual(r.status_code, 201)
            self.assertEqual(self.c.post("/api/sessions", json={**base, "pengawas_ref": "zzz"}).status_code, 422)
        finally:
            with SessionLocal() as db:
                db.query(Pengawas).filter(Pengawas.id.in_(ids)).delete(synchronize_session=False)
                db.commit()

    def test_session_submit_syncs_contact_back_into_pengawas_table(self):
        """Buat/ubah sesi ikut menyimpan nama+hp pengawas balik ke tabel pengawas (server/main.py
        _sync_pengawas_contact) -- supaya hp yg baru diisi sendiri (needs_hp) & pengawas manual
        ("Lainnya -- isi sendiri") ikut terdaftar tanpa admin perlu psql manual."""
        from server.db import Pengawas, SessionLocal
        base = {"kelas": VALID["kelas"], "prodi": VALID["prodi"], "fakultas": VALID["fakultas"], "hari_ujian": VALID["hari_ujian"], "kode_soal": VALID["kode_soal"]}
        no_hp_id, has_hp_id = "tes_sync_nohp", "tes_sync_hashp"
        with SessionLocal() as db:
            db.add_all([
                Pengawas(id=no_hp_id, nama="Tes Sync Kosong", nim="900101", hp=""),
                Pengawas(id=has_hp_id, nama="Tes Sync Terisi", nim="900102", hp="+6281111111111"),
            ])
            db.commit()
        try:
            # (1) pengawas terdaftar BELUM py hp, isi sendiri saat submit -> tersimpan ke tabel pengawas
            r = self.c.post("/api/sessions", json={**base, "pengawas_ref": no_hp_id, "hp": "0812340000001"})
            self.assertEqual(r.status_code, 201, r.text)
            with SessionLocal() as db:
                self.assertEqual(db.get(Pengawas, no_hp_id).hp, "+62812340000001")
            # (2) pengawas yg SUDAH py hp -- hp baru dari klien TAK menimpa yg sudah tersimpan
            r = self.c.post("/api/sessions", json={**base, "pengawas_ref": has_hp_id, "hp": "0899999999999"})
            self.assertEqual(r.status_code, 201, r.text)
            with SessionLocal() as db:
                self.assertEqual(db.get(Pengawas, has_hp_id).hp, "+6281111111111")
            # (3) "Lainnya -- isi sendiri" (pengawas_ref kosong) -> jadi baris BARU di tabel pengawas
            manual_nama = "Tes Sync Manual Baru"
            r = self.c.post("/api/sessions", json={**base, "nama_pengawas": manual_nama, "hp": "0812340000002"})
            self.assertEqual(r.status_code, 201, r.text)
            with SessionLocal() as db:
                row = db.query(Pengawas).filter_by(nama=manual_nama).one()
                self.assertEqual(row.hp, "+62812340000002")
            # (4) submit manual dgn nama SAMA lagi -> TAK menggandakan baris
            r = self.c.post("/api/sessions", json={**base, "nama_pengawas": manual_nama, "hp": "0812340000002"})
            self.assertEqual(r.status_code, 201, r.text)
            with SessionLocal() as db:
                self.assertEqual(db.query(Pengawas).filter_by(nama=manual_nama).count(), 1)
        finally:
            with SessionLocal() as db:
                db.query(Pengawas).filter(Pengawas.id.in_([no_hp_id, has_hp_id])).delete(synchronize_session=False)
                db.query(Pengawas).filter_by(nama=manual_nama).delete(synchronize_session=False)
                db.commit()

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
        self.assertNotIn("nama", d["sheets"][0])  # nama mahasiswa: tersimpan di rekap, tak dikirim ke pengawas

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

    def test_correct_sheet_npm_kode_fakultas(self):
        # sesi A: hanya menguji PATCH (tidak pernah disubmit) -- jangan mengubah data lembar yang disubmit,
        # supaya tidak ikut mengotori export.csv yang dipakai tes lain (mis. test_full_flow_matches_golden,
        # yang mengambil baris PERTAMA dan mengasumsikan itu berasal dari LJK.pdf kosong tanpa koreksi manual).
        sid = self.c.post("/api/sessions", json=VALID).json()["id"]
        data = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("LJK.pdf", data, "application/pdf"))])
        d = self.wait(sid)
        shid = d["sheets"][0]["id"]

        opts = self.c.get("/api/meta").json()["fakultas"]
        self.assertIn("FIF", opts)

        r = self.c.patch(f"/api/sheets/{shid}", json={"npm": "10 32 60 01 23"})
        self.assertEqual((r.status_code, r.json()["npm"]), (200, "1032600123"))
        self.assertEqual(self.c.patch(f"/api/sheets/{shid}", json={"npm": "123"}).status_code, 422)  # bukan 10 digit

        r = self.c.patch(f"/api/sheets/{shid}", json={"kode_soal": "273"})
        self.assertEqual((r.status_code, r.json()["kode_soal"]), (200, "273"))
        self.assertEqual(self.c.patch(f"/api/sheets/{shid}", json={"kode_soal": "27"}).status_code, 422)  # bukan 3 digit

        r = self.c.patch(f"/api/sheets/{shid}", json={"fakultas_ljk": "fit"})
        self.assertEqual((r.status_code, r.json()["fakultas_ljk"]), (200, "FIT"))
        self.assertEqual(self.c.patch(f"/api/sheets/{shid}", json={"fakultas_ljk": "ZZZ"}).status_code, 422)

        # nilai ikut terhitung ulang dgn kunci saat ini (273 belum ada kunci terpasang di kode itu)
        r = self.c.patch(f"/api/sheets/{shid}", json={"kode_soal": "192"})
        self.assertEqual(r.status_code, 200)

        # sesi B: dibiarkan apa adanya lalu disubmit, khusus menguji bahwa koreksi ditolak setelah submit
        sid2 = self.c.post("/api/sessions", json=VALID).json()["id"]
        self.c.post(f"/api/sessions/{sid2}/files", files=[("files", ("LJK.pdf", data, "application/pdf"))])
        d2 = self.wait(sid2)
        shid2 = d2["sheets"][0]["id"]
        self.c.post(f"/api/sessions/{sid2}/validate-all")
        self.c.post(f"/api/sessions/{sid2}/submit")
        self.assertEqual(self.c.patch(f"/api/sheets/{shid2}", json={"npm": "1032600124"}).status_code, 409)

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

    def test_files_queue_shows_pending_names_then_empties(self):
        """Antrean per-berkas (queued/processing): pengawas bisa lihat berkas MANA yg masih diproses,
        bukan cuma jumlahnya -- penting krn sebagian lembar sulit bisa lama dipindai."""
        sid = self.c.post("/api/sessions", json=VALID).json()["id"]
        data = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()
        files = [("files", (f"q{i}.pdf", data, "application/pdf")) for i in range(6)]
        self.c.post(f"/api/sessions/{sid}/files", files=files)
        d0 = self.c.get(f"/api/sessions/{sid}").json()
        self.assertEqual(len(d0["files"]["queue"]), d0["files"]["pending"])
        for item in d0["files"]["queue"]:
            self.assertEqual(set(item), {"name", "state"})
            self.assertIn(item["state"], ("queued", "processing"))
            self.assertTrue(item["name"].startswith("q"))
        d = self.wait(sid)
        self.assertEqual((d["files"]["queue"], d["files"]["pending"]), ([], 0))


if __name__ == "__main__":
    unittest.main()
