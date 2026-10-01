"""Uji layanan: nilai ulang saat submit, sinkron Google Sheet (outbox + retry + batch), kunci, admin, ekspor, pembersihan.

Google Sheet dipalsukan (FakeSheets) — tidak ada panggilan jaringan.
"""
import csv
import datetime as dt
import io
import os
import time
import unittest
import zipfile

from fastapi.testclient import TestClient

from server import auth, config, services, sheets
from server.db import AdminUser, ScanSession, Sheet, SessionLocal, TemplateCalib
from server.main import app
from tests.regression.fixtures import KUNCI

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ADM = {"X-Admin-Token": "rahasia"}
VALID = {"nama_pengawas": "Budi Santoso", "hp": "081234567890", "ruangan": "TULT 0603", "kelas": "BS1SI-50-REG-01",
         "prodi": "S1 Sistem Informasi", "fakultas": "FIF", "hari_ujian": "2026-09-28", "kode_soal": "A"}
PDF = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()


class FakeSheets:
    def __init__(self):
        self.calls, self.fail, self.kunci = [], 0, {}
        self.rows_by_npm = {}   # simulasi isi Sheet (npm -> record terakhir) -- utk uji upsert_records

    def append_records(self, recs):
        if self.fail > 0:
            self.fail -= 1
            raise RuntimeError("429 quota exceeded")
        self.calls.append(list(recs))
        for r in recs:
            npm = str(r.get("NPM", "") or "")
            if npm:
                self.rows_by_npm[npm] = r
        return len(recs)

    def upsert_records(self, recs, key_col="NPM"):
        if self.fail > 0:
            self.fail -= 1
            raise RuntimeError("429 quota exceeded")
        self.calls.append(list(recs))
        updated = appended = 0
        for r in recs:
            key = str(r.get(key_col, "") or "")
            if key and key in self.rows_by_npm:
                updated += 1
            else:
                appended += 1
            if key:
                self.rows_by_npm[key] = r
        return {"updated": updated, "appended": appended}

    def fetch_kunci(self):
        return self.kunci


class TestServices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from server import refdata
        cls._orig_kp, refdata.kelas_prodi = refdata.kelas_prodi, lambda k: VALID["prodi"]   # tes memakai nama kelas bebas
        cls._ctx = TestClient(app)
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        from server import refdata
        refdata.kelas_prodi = cls._orig_kp
        sheets.set_client(None)
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        self.fake = FakeSheets()
        sheets.set_client(self.fake)
        self.drain()

    def drain(self):
        """Isolasi antar tes: anggap semua sesi lama sudah tersinkron & hapus semua kunci + kalibrasi."""
        from server.db import Kunci
        with SessionLocal() as db:
            for s in db.query(ScanSession).filter(ScanSession.submitted.is_(True), ScanSession.synced_at.is_(None)):
                s.synced_at = dt.datetime.now(dt.timezone.utc)
            db.query(Kunci).delete()
            db.query(TemplateCalib).delete()
            db.commit()
        self.fake.calls.clear()

    def submitted_session(self, kelas="IF-47-01", n=1):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": kelas}).json()["id"]
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", (f"l{i}.pdf", PDF, "application/pdf")) for i in range(n)])
        t = time.time()
        while time.time() - t < 120 and self.c.get(f"/api/sessions/{sid}").json()["scanning"]:
            time.sleep(0.4)
        self.c.post(f"/api/sessions/{sid}/validate-all")
        return sid

    def submit(self, sid):
        r = self.c.post(f"/api/sessions/{sid}/submit")
        self.assertEqual(r.status_code, 200, r.text)

    def test_monitor_progres_per_hari(self):
        from server import plotting
        csv_text = ("No,Hari,Jam Mulai,Jam Selesai,Gedung,Ruangan,Kelas,Prodi,Jml Mahasiswa,Nama Pengawas,Cek Bentrok,Mode,\n"
                    "1,SENIN,08:30,09:30,KU1,R1,MON-A,S1 X,3,Budi Santoso,OK,Onsite,\n"
                    "2,SENIN,09:30,10:30,KU1,R2,MON-B,S1 X,2,Ani,OK,Onsite,\n"
                    "3,SELASA,08:30,09:30,KU1,R3,MON-C,S1 X,4,Cici,OK,Onsite,\n"
                    "4,SENIN,08:30,09:30,Online,-,MON-D,S1 X,9,Dedi,OK,Online,\n")
        old = (plotting._download, config.MONITOR_SINCE, config.UPLOAD_DIR)
        plotting._download = lambda: csv_text
        config.MONITOR_SINCE = ""
        plotting._cache.update(rows=None, at=0.0, error=None)
        try:
            sid = self.submitted_session("MON-A", n=2)
            self.submit(sid)
            self.c.post("/api/sessions", json={**VALID, "kelas": "MON-B"})
            extra = self.submitted_session("LUAR-JADWAL", n=1)
            self.submit(extra)
            d = self.c.get("/api/admin/monitor?refresh=true", headers=ADM).json()
            self.assertEqual(d["overall"]["total"], 3)                      # baris Online tidak dihitung
            self.assertEqual(d["overall"]["selesai"], 1)
            self.assertEqual(d["overall"]["berjalan"], 1)                   # MON-B: sesi dibuat tapi belum submit
            senin = next(x for x in d["days"] if x["hari"] == "SENIN")
            a = next(x for x in senin["slots"] if x["kelas"] == "MON-A")
            self.assertEqual((a["status"], a["lembar"], a["jml_mhs"]), ("selesai", 2, 3))
            self.assertEqual(senin["mhs_upload"], 2)
            self.assertEqual(senin["pct_mhs"], round(2 / 5 * 100, 1))
            self.assertTrue(any(e["kelas"] == "LUAR-JADWAL" for e in d["di_luar_jadwal"]))
            self.assertEqual(self.c.get("/api/admin/monitor?mode=online&refresh=true", headers=ADM).json()["overall"]["total"], 1)
            self.assertEqual(self.c.get("/api/admin/monitor").status_code, 401)
        finally:
            plotting._download, config.MONITOR_SINCE, config.UPLOAD_DIR = old
            plotting._cache.update(rows=None, at=0.0, error=None)

    def test_admin_requires_token(self):
        for path in ("/api/admin/summary", "/api/admin/sessions", "/api/admin/kunci", "/api/admin/export.xlsx"):
            self.assertEqual(self.c.get(path).status_code, 401, path)
            self.assertEqual(self.c.get(path, headers={"X-Admin-Token": "salah"}).status_code, 401, path)

    def test_regrade_at_submit_uses_latest_key(self):
        sid = self.submitted_session()   # dipindai tanpa kunci
        before = self.c.get("/api/admin/export.csv", headers=ADM).text
        self.c.put("/api/admin/kunci/A", json={str(q): a for q, a in KUNCI["A"].items()}, headers=ADM)   # kunci menyusul
        self.submit(sid)
        rows = [r for r in csv.DictReader(io.StringIO(self.c.get(f"/api/admin/export.csv?sid={sid}", headers=ADM).text))]
        self.assertEqual(len(rows), 1)
        self.assertNotIn(rows[0]["Nilai"], ("-", ""), rows[0])
        self.assertEqual(rows[0]["Kunci Terpakai"], "A")

    def test_sync_batches_all_due_sessions_in_one_call(self):
        s1, s2 = self.submitted_session("K1"), self.submitted_session("K2", n=2)
        self.submit(s1); self.submit(s2)
        r = services.sync_pending_once()
        self.assertEqual((r["synced"], len(self.fake.calls)), (2, 1))          # SATU permintaan untuk 2 sesi
        self.assertEqual(len(self.fake.calls[0]), 3)                           # 1 + 2 lembar
        self.assertEqual({x["Kelas"] for x in self.fake.calls[0]}, {"K1", "K2"})
        self.assertEqual(services.sync_pending_once()["synced"], 0)            # tidak dikirim dua kali
        items = self.c.get("/api/admin/sessions?status=submitted", headers=ADM).json()["items"]
        self.assertTrue(all(i["synced"] for i in items if i["id"] in (s1, s2)))

    def test_sync_failure_backoff_then_recover(self):
        sid = self.submitted_session("K3")
        self.submit(sid)
        self.fake.fail = 1
        r = services.sync_pending_once()
        self.assertEqual((r["synced"], r["failed"]), (0, 1))
        me = next(i for i in self.c.get("/api/admin/sessions?status=unsynced", headers=ADM).json()["items"] if i["id"] == sid)
        self.assertEqual((me["synced"], me["sync_attempts"]), (False, 1))
        self.assertIn("429", me["sync_error"])
        self.assertEqual(services.sync_pending_once()["synced"], 0)            # masih masa tunggu (backoff)
        self.assertEqual(self.c.post("/api/admin/sync/run", headers=ADM).json()["synced"], 1)   # admin paksa ulang
        self.assertEqual(len(self.fake.calls), 1)

    def test_poison_session_does_not_block_others(self):
        good, bad = self.submitted_session("OK1"), self.submitted_session("BAD1")
        self.submit(good); self.submit(bad)
        orig = self.fake.append_records

        def picky(recs):
            if any(r.get("Kelas") == "BAD1" for r in recs):
                raise RuntimeError("baris tidak valid")
            return orig(recs)
        self.fake.append_records = picky
        r = services.sync_pending_once()
        self.assertEqual((r["synced"], r["failed"]), (1, 1))
        st = {i["id"]: i["synced"] for i in self.c.get("/api/admin/sessions", headers=ADM).json()["items"]}
        self.assertEqual((st[good], st[bad]), (True, False))

    def test_sync_since_skips_older_sessions(self):
        sid = self.submitted_session("SINCE1")
        self.submit(sid)
        old = config.SYNC_SINCE
        try:
            config.SYNC_SINCE = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
            self.assertEqual(services.sync_pending_once()["synced"], 0)        # sesi lebih lama dari batas -> dilewati
            self.assertEqual(len(self.fake.calls), 0)
            config.SYNC_SINCE = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            self.assertEqual(services.sync_pending_once()["synced"], 1)        # setelah batas -> terkirim
        finally:
            config.SYNC_SINCE = old

    def test_toml_credentials_reuse_streamlit_secrets(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[connections.gsheets]\nspreadsheet = "https://docs.google.com/spreadsheets/d/ABC/edit"\n'
                    'type = "service_account"\nproject_id = "p"\nclient_email = "x@p.iam.gserviceaccount.com"\nprivate_key = "K"\n')
        creds, url = sheets.load_toml_credentials(f.name)
        os.unlink(f.name)
        self.assertEqual(url, "https://docs.google.com/spreadsheets/d/ABC/edit")
        self.assertEqual(creds["client_email"], "x@p.iam.gserviceaccount.com")
        self.assertNotIn("spreadsheet", creds)                                  # bukan bagian kredensial

    def test_sync_not_configured_is_noop(self):
        sheets.set_client(None)
        self.assertEqual(services.sync_pending_once()["configured"], False)
        self.assertFalse(self.c.get("/api/admin/summary", headers=ADM).json()["gsheet_configured"])

    def test_kunci_sync_upload_delete(self):
        self.fake.kunci = {"kj048": {1: "A", 2: "B", 3: "C", 4: "D"}}
        r = self.c.post("/api/admin/kunci/sync", headers=ADM).json()
        self.assertEqual(r["kunci"], 1)
        lst = {k["name"]: k for k in self.c.get("/api/admin/kunci", headers=ADM).json()}
        self.assertEqual((lst["kj048"]["soal"], lst["kj048"]["source"]), (4, "gsheet"))
        up = self.c.post("/api/admin/kunci/upload", headers=ADM, files={"file": ("kj999.csv", b"1,A\n2,B\n3,C\n4,D\n5,A\n", "text/csv")})
        self.assertEqual(up.status_code, 200, up.text)
        self.assertEqual(up.json()["kunci"], {"kj999": 5})
        self.assertEqual(self.c.post("/api/admin/kunci/upload", headers=ADM, files={"file": ("x.csv", b"halo", "text/csv")}).status_code, 422)
        self.assertEqual(self.c.post("/api/admin/kunci/upload", headers=ADM, files={"file": ("x.txt", b"1,A", "text/plain")}).status_code, 415)
        self.assertEqual(self.c.delete("/api/admin/kunci/kj999", headers=ADM).status_code, 204)
        self.assertEqual(self.c.delete("/api/admin/kunci/kj999", headers=ADM).status_code, 404)

    def test_admin_validate_session_requires_scan_done_then_toggles(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMV-01"}).json()["id"]
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        # masih scanning -> admin belum boleh menandai validated (mencegah lembar yg belum sempat masuk
        # daftar tersembunyi di balik status "validated").
        r = self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)
        self.assertEqual(r.status_code, 409, r.text)
        t = time.time()
        while time.time() - t < 120 and self.c.get(f"/api/sessions/{sid}").json()["scanning"]:
            time.sleep(0.4)
        rows = self.c.get(f"/api/admin/sessions?q=ADMV-01", headers=ADM).json()["items"]
        self.assertEqual(next(x for x in rows if x["id"] == sid)["status"], "perlu_cek")
        r = self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)
        self.assertEqual((r.status_code, r.json()["admin_validated"], r.json()["submitted"]), (200, True, True))
        self.assertEqual(r.json()["admin_validated_by"], "ADMIN_TOKEN")   # identitas admin yg menandai (lihat auth.require_admin)
        # validasi admin JUGA memfinalisasi sesi (dulu tugas pengawas via submit()): lembar yg berhasil discan
        # otomatis tervalidasi & sesi terkunci (submitted) -- pengawas tak perlu apa2 lagi.
        d = self.c.get(f"/api/sessions/{sid}").json()
        self.assertTrue(d["submitted"])
        self.assertTrue(all(x["validated"] for x in d["sheets"]))
        rows = self.c.get("/api/admin/sessions?status=validated", headers=ADM).json()["items"]
        self.assertIn(sid, [x["id"] for x in rows])
        self.assertEqual(next(x for x in rows if x["id"] == sid)["admin_validated_by"], "ADMIN_TOKEN")
        self.assertNotIn(sid, [x["id"] for x in self.c.get("/api/admin/sessions?status=perlu_cek", headers=ADM).json()["items"]])
        # batalkan tanda -- tak menyentuh submitted (sinkron mungkin sudah berjalan; lihat docstring), tapi
        # "divalidasi oleh" ikut dikosongkan (bukan validated lagi, jadi tak relevan menyebut siapa yg dulu menandai)
        r = self.c.post(f"/api/admin/sessions/{sid}/validate?value=false", headers=ADM)
        self.assertEqual((r.status_code, r.json()["admin_validated"], r.json()["submitted"]), (200, False, True))
        self.assertIsNone(r.json()["admin_validated_by"])

    def test_admin_validate_session_requires_at_least_one_sheet(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMV-EMPTY"}).json()["id"]
        r = self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)
        self.assertEqual(r.status_code, 409, r.text)

    def test_public_monitor_sesi_lists_status_without_login_or_phone(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMMON-01", "kode_soal": "A", "hari_ujian": "2026-09-30"}).json()["id"]
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        r = self.c.get("/api/monitor-sesi")   # tanpa token
        self.assertEqual(r.status_code, 200, r.text)
        row = next(x for x in r.json()["items"] if x["kelas"] == "ADMMON-01")
        self.assertEqual((row["hari_ujian"], row["kode_soal"], row["status"]), ("2026-09-30", "A", "scanning"))
        self.assertNotIn("hp", row)
        t = time.time()
        while time.time() - t < 120 and self.c.get(f"/api/sessions/{sid}").json()["scanning"]:
            time.sleep(0.4)
        row = next(x for x in self.c.get("/api/monitor-sesi").json()["items"] if x["kelas"] == "ADMMON-01")
        self.assertEqual(row["status"], "perlu_cek")

    def test_admin_validate_requires_token(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMV-02"}).json()["id"]
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/validate").status_code, 401)
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/stop").status_code, 401)
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/clear-photos").status_code, 401)
        self.assertEqual(self.c.delete(f"/api/admin/sessions/{sid}").status_code, 401)

    def test_admin_clear_photos_requires_validated_then_removes_folder_keeps_records(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMCLR-01"}).json()["id"]
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        t = time.time()
        while time.time() - t < 120 and self.c.get(f"/api/sessions/{sid}").json()["scanning"]:
            time.sleep(0.4)
        with SessionLocal() as db:
            paths = [f.path for f in db.get(ScanSession, sid).files]
        self.assertTrue(paths and all(os.path.exists(p) for p in paths))
        shid = self.c.get(f"/api/sessions/{sid}").json()["sheets"][0]["id"]
        # belum admin_validated -> ditolak
        r = self.c.post(f"/api/admin/sessions/{sid}/clear-photos", headers=ADM)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertTrue(all(os.path.exists(p) for p in paths))
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM).status_code, 200)
        r = self.c.post(f"/api/admin/sessions/{sid}/clear-photos", headers=ADM)
        self.assertEqual((r.status_code, r.json()["photos_cleared"]), (200, True))
        self.assertFalse(any(os.path.exists(p) for p in paths))
        # rekap/DB tetap utuh: sesi & lembar masih ada
        d = self.c.get(f"/api/sessions/{sid}").json()
        self.assertEqual(len(d["sheets"]), 1)
        row = next(x for x in self.c.get(f"/api/admin/sessions?q=ADMCLR-01", headers=ADM).json()["items"] if x["id"] == sid)
        self.assertTrue(row["photos_cleared"])
        # pratinjau lembar sekarang 410 (berkas sumber sudah tak ada) -- ditangani, bukan error 500
        self.assertEqual(self.c.get(f"/api/sheets/{shid}/preview").status_code, 410)

    def test_admin_stop_session_cancels_pending_files(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMSTOP-01"}).json()["id"]
        # lebih banyak berkas drpd SCAN_WORKERS (2 di tes) supaya sebagian PASTI masih menunggu di antrean
        # pool saat stop dipanggil, bukan cuma sedang benar2 dieksekusi worker.
        self.c.post(f"/api/sessions/{sid}/files",
                    files=[("files", (f"s{i}.pdf", PDF, "application/pdf")) for i in range(10)])
        r = self.c.post(f"/api/admin/sessions/{sid}/stop", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["dibatalkan"] + body["masih_berjalan"], 10)
        self.assertGreaterEqual(body["dibatalkan"], 1, body)   # setidaknya sebagian sempat dibatalkan
        t = time.time()
        while time.time() - t < 120 and self.c.get(f"/api/sessions/{sid}").json()["scanning"]:
            time.sleep(0.4)
        d = self.c.get(f"/api/sessions/{sid}").json()
        cancelled = [f for f in d["files"]["failed"] if f["error"] == "Dibatalkan oleh admin"]
        self.assertEqual(len(cancelled), body["dibatalkan"])

    def test_admin_delete_session_removes_row_and_files(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMDEL-01"}).json()["id"]
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        t = time.time()
        while time.time() - t < 120 and self.c.get(f"/api/sessions/{sid}").json()["scanning"]:
            time.sleep(0.4)
        with SessionLocal() as db:
            paths = [f.path for f in db.get(ScanSession, sid).files]
        self.assertTrue(paths and all(os.path.exists(p) for p in paths))
        r = self.c.delete(f"/api/admin/sessions/{sid}", headers=ADM)
        self.assertEqual(r.status_code, 204, r.text)
        self.assertEqual(self.c.get(f"/api/sessions/{sid}").status_code, 404)
        self.assertFalse(any(os.path.exists(p) for p in paths))
        self.assertEqual(self.c.delete(f"/api/admin/sessions/{sid}", headers=ADM).status_code, 404)

    def test_admin_delete_session_keeps_other_sessions_photos_in_shared_class_folder(self):
        # Dua sesi kelas SAMA (fakultas/prodi/kelas sama) -> folder foto DIBAGI (lihat _class_folder).
        # Hapus satu sesi tak boleh ikut menghapus foto sesi lain di kelas yg sama.
        a = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMDEL-SHARED"}).json()["id"]
        b = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMDEL-SHARED"}).json()["id"]
        self.c.post(f"/api/sessions/{a}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        self.c.post(f"/api/sessions/{b}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        t = time.time()
        while time.time() - t < 120 and (self.c.get(f"/api/sessions/{a}").json()["scanning"] or self.c.get(f"/api/sessions/{b}").json()["scanning"]):
            time.sleep(0.4)
        with SessionLocal() as db:
            a_paths = [f.path for f in db.get(ScanSession, a).files]
            b_paths = [f.path for f in db.get(ScanSession, b).files]
        self.assertTrue(a_paths and b_paths and os.path.dirname(a_paths[0]) == os.path.dirname(b_paths[0]))
        self.assertEqual(self.c.delete(f"/api/admin/sessions/{a}", headers=ADM).status_code, 204)
        self.assertFalse(any(os.path.exists(p) for p in a_paths))
        self.assertTrue(all(os.path.exists(p) for p in b_paths))   # sesi B tak tersentuh

    def test_export_xlsx_and_filter(self):
        sid = self.submitted_session("XL-01")
        self.submit(sid)
        r = self.c.get("/api/admin/export.xlsx?kelas=XL-01", headers=ADM)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(r.content)))
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(r.content)).active
        header = [c.value for c in ws[1]]
        self.assertEqual(header[:5], ["Submit Date", "Nama Pengawas", "No HP Pengawas", "Ruangan", "Kelas"])
        self.assertEqual({row[header.index("Kelas")].value for row in ws.iter_rows(min_row=2)}, {"XL-01"})

    def test_regrade_all_after_key_change(self):
        sid = self.submitted_session("RG-01")
        self.submit(sid)                                   # disubmit TANPA kunci -> Nilai "-"
        def nilai():
            rows = list(csv.DictReader(io.StringIO(self.c.get(f"/api/admin/export.csv?sid={sid}", headers=ADM).text)))
            return rows[0]["Nilai"], rows[0]["Kunci Terpakai"]
        self.assertEqual(self.c.post("/api/admin/regrade", headers=ADM).json()["error"], "Belum ada kunci jawaban")
        self.assertEqual(nilai()[0], "-")
        self.c.put("/api/admin/kunci/A", json={str(q): a for q, a in KUNCI["A"].items()}, headers=ADM)
        r1 = self.c.post("/api/admin/regrade?kelas=RG-01", headers=ADM).json()
        self.assertEqual((r1["sessions"], r1["changed"], r1["changed_in_synced"]), (1, 1, 0))
        self.assertNotEqual(nilai()[0], "-")
        self.assertEqual(self.c.post("/api/admin/regrade?kelas=RG-01", headers=ADM).json()["changed"], 0)   # idempoten
        # kunci berubah -> nilai berubah; sesi yang sudah terkirim ke Sheet dilaporkan usang
        services.sync_pending_once()
        before = nilai()[0]
        self.c.put("/api/admin/kunci/A", json={str(q): "D" for q in range(1, 76)}, headers=ADM)
        r2 = self.c.post("/api/admin/regrade?kelas=RG-01", headers=ADM).json()
        self.assertEqual((r2["changed"], r2["changed_in_synced"]), (1, 1))
        self.assertNotEqual(nilai()[0], before)
        # filter kelas lain tidak tersentuh
        self.assertEqual(self.c.post("/api/admin/regrade?kelas=TIDAK-ADA", headers=ADM).json()["sessions"], 0)

    def test_regrade_pull_from_sheet(self):
        self.fake.kunci = {"kj777": {q: "A" for q in range(1, 11)}}
        r = self.c.post("/api/admin/regrade?pull=true", headers=ADM).json()
        self.assertEqual((r["gsheet_configured"], r["kunci_ditarik"]), (True, 1))

    def test_record_key_order_survives_database_roundtrip(self):
        """Urutan kolom rekap (ekspor/Sheet) bergantung pada urutan kunci JSON. PostgreSQL `jsonb` mengacaknya; kolom JSON biasa tidak."""
        from server.db import Sheet, UploadFile, engine
        sid = self.submitted_session("ORD-1")
        order = ["Zebra", "Alpha", "Nama Pengawas", "NPM", "b", "aa", "Kelas"]      # sengaja bukan alfabet/panjang
        with SessionLocal() as db:
            sh = db.query(Sheet).filter(Sheet.session_id == sid).first()
            sh.record = {k: "x" for k in order}
            db.commit()
        with SessionLocal() as db:
            got = list(db.query(Sheet).filter(Sheet.session_id == sid).first().record.keys())
        self.assertEqual(got, order, f"urutan kunci berubah di {engine.dialect.name}")

    def test_cleanup_removes_old_upload_folders_only(self):
        # Foto kini disimpan per Fakultas/Prodi/Kelas (dibagi antar sesi sekelas -- lihat _class_folder di
        # server/main.py), bukan lagi per id sesi -- jadi pembersihan dicek per BERKAS (UploadFile.path),
        # bukan per folder bernama id sesi.
        old, fresh = self.submitted_session("OLD"), self.submitted_session("NEW")
        self.submit(old); self.submit(fresh)
        with SessionLocal() as db:
            db.get(ScanSession, old).submitted_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=config.UPLOAD_RETENTION_HOURS + 2)
            db.commit()
        with SessionLocal() as db:
            old_paths = [f.path for f in db.get(ScanSession, old).files]
            fresh_paths = [f.path for f in db.get(ScanSession, fresh).files]
        self.assertTrue(old_paths and all(os.path.exists(p) for p in old_paths))
        services.cleanup_once()
        self.assertFalse(any(os.path.exists(p) for p in old_paths))
        self.assertTrue(fresh_paths and all(os.path.exists(p) for p in fresh_paths))

    def test_cleanup_disabled_removes_nothing(self):
        old = self.submitted_session("DISABLEDCLEAN")
        self.submit(old)
        with SessionLocal() as db:
            db.get(ScanSession, old).submitted_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=config.UPLOAD_RETENTION_HOURS + 2)
            db.commit()
        with SessionLocal() as db:
            paths = [f.path for f in db.get(ScanSession, old).files]
        old_flag = config.CLEANUP_ENABLED
        config.CLEANUP_ENABLED = False
        try:
            r = services.cleanup_once()
            self.assertEqual(r, {"files_removed": 0, "disabled": True})
            self.assertTrue(paths and all(os.path.exists(p) for p in paths))
        finally:
            config.CLEANUP_ENABLED = old_flag

    def test_admin_session_sheets_shows_student_fill_detail(self):
        sid = self.submitted_session("ADMSHEETS-01")
        r = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["items"]
        self.assertEqual(len(items), 1)
        x = items[0]
        for k in ("seq", "file", "nama", "npm", "kode_soal", "fakultas_ljk", "terisi", "nilai", "label", "validated", "photo_exists"):
            self.assertIn(k, x)
        self.assertTrue(x["photo_exists"])
        self.assertEqual(self.c.get(f"/api/admin/sessions/{sid}/sheets").status_code, 401)   # tanpa token

    def test_admin_session_sheets_404_for_unknown_session(self):
        self.assertEqual(self.c.get("/api/admin/sessions/tidak-ada/sheets", headers=ADM).status_code, 404)

    def test_admin_sheet_photo_fast_original_preview(self):
        sid = self.submitted_session("ADMPHOTO-01")
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        r = self.c.get(f"/api/admin/sheets/{shid}/photo", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertGreater(len(r.content), 100)
        self.assertEqual(self.c.get(f"/api/admin/sheets/{shid}/photo").status_code, 401)   # tanpa token
        self.assertEqual(self.c.get("/api/admin/sheets/tidak-ada/photo", headers=ADM).status_code, 404)

    def test_admin_rescan_sheet_updates_record_even_after_submit(self):
        sid = self.submitted_session("ADMRESCAN-01")
        self.submit(sid)   # sesi terkunci -- endpoint admin tak boleh terhalang spt endpoint pengawas
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        r = self.c.post(f"/api/admin/sheets/{shid}/rescan", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("label", r.json())
        self.assertEqual(self.c.post(f"/api/admin/sheets/{shid}/rescan").status_code, 401)   # tanpa token
        self.assertEqual(self.c.post("/api/admin/sheets/tidak-ada/rescan", headers=ADM).status_code, 404)

    def test_admin_replace_sheet_photo_works_even_after_submit(self):
        sid = self.submitted_session("ADMREPL-01")
        self.submit(sid)
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        r = self.c.post(f"/api/admin/sheets/{shid}/replace", headers=ADM, files={"file": ("baru.pdf", PDF, "application/pdf")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("label", r.json())

    def test_upload_path_organized_by_fakultas_prodi_kelas(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ORGTEST-01"}).json()["id"]
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        with SessionLocal() as db:
            path = db.get(ScanSession, sid).files[0].path
        rel = os.path.relpath(path, config.UPLOAD_DIR)
        parts = rel.split(os.sep)
        self.assertEqual(parts[:3], [VALID["fakultas"], VALID["prodi"], "ORGTEST-01"])

    def test_migrate_storage_moves_old_per_session_folder_into_new_hierarchy(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "MIGTEST-01"}).json()["id"]
        self.c.post(f"/api/sessions/{sid}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        # Simulasikan berkas dari SEBELUM migrasi ke struktur Fakultas/Prodi/Kelas: dulu foldernya cuma
        # UPLOAD_DIR/{session_id}/... -- pindahkan manual berkas yg baru saja dibuat itu ke sana & catat di DB,
        # persis kondisi peninggalan sesi lama di produksi.
        with SessionLocal() as db:
            up = db.get(ScanSession, sid).files[0]
            old_dir = os.path.join(config.UPLOAD_DIR, sid)
            os.makedirs(old_dir, exist_ok=True)
            old_path = os.path.join(old_dir, os.path.basename(up.path))
            os.replace(up.path, old_path)
            content_before = open(old_path, "rb").read()
            up.path = old_path
            db.commit()

        r = self.c.post("/api/admin/migrate-storage", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        # Nilai global (dipindah/sudah_benar) merangkum SELURUH UploadFile di DB tes ini (dipakai bersama
        # tes lain) -- jangan diasumsikan tepat 1; yg dicek presisi adalah berkas SESI INI sendiri, di bawah.
        self.assertGreaterEqual(r.json()["dipindah"], 1)
        self.assertEqual(r.json()["gagal"], 0)

        with SessionLocal() as db:
            new_path = db.get(ScanSession, sid).files[0].path
        rel = os.path.relpath(new_path, config.UPLOAD_DIR)
        self.assertEqual(rel.split(os.sep)[:3], [VALID["fakultas"], VALID["prodi"], "MIGTEST-01"])
        self.assertTrue(os.path.exists(new_path))
        self.assertEqual(open(new_path, "rb").read(), content_before)   # isi berkas tak berubah, cuma lokasinya
        self.assertFalse(os.path.exists(old_dir))   # folder lama yg jadi kosong ikut dibersihkan

        # dipanggil lagi -> idempoten, berkas sesi ini spesifik tak ikut "dipindah" lagi (sudah di lokasi benar)
        r2 = self.c.post("/api/admin/migrate-storage", headers=ADM)
        self.assertEqual(r2.json()["dipindah"], 0)
        self.assertGreaterEqual(r2.json()["sudah_benar"], 1)
        with SessionLocal() as db:
            self.assertEqual(db.get(ScanSession, sid).files[0].path, new_path)   # tak berubah lagi

        self.assertEqual(self.c.post("/api/admin/migrate-storage").status_code, 401)

    def test_kelas_check_reports_existing_session_then_excludes_self(self):
        sid1 = self.c.post("/api/sessions", json={**VALID, "kelas": "KCHECK-01"}).json()["id"]
        self.c.post(f"/api/sessions/{sid1}/files", files=[("files", ("l.pdf", PDF, "application/pdf"))])
        r = self.c.get("/api/kelas-check", params={"fakultas": VALID["fakultas"], "prodi": VALID["prodi"], "kelas": "KCHECK-01"})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertTrue(d["exists"])
        self.assertEqual(d["sesi"], 1)
        self.assertEqual(d["pengawas_terakhir"], VALID["nama_pengawas"])
        r2 = self.c.get("/api/kelas-check", params={"fakultas": VALID["fakultas"], "prodi": VALID["prodi"], "kelas": "KCHECK-01", "exclude_sid": sid1})
        self.assertFalse(r2.json()["exists"])   # sesi itu sendiri dikecualikan
        r3 = self.c.get("/api/kelas-check", params={"fakultas": VALID["fakultas"], "prodi": VALID["prodi"], "kelas": "KCHECK-BELUM-ADA"})
        self.assertFalse(r3.json()["exists"])

    def _make_orphan_file(self, kelas):
        """Sesi dgn 1 berkas yg SUKSES scan (1 sheet), lalu simulasikan bug "hilang senyap" yg ditemukan
        29 Sep 2026: baris Sheet dihapus manual tapi UploadFile.state TETAP 'done' -- persis kondisi yg
        ditemukan di produksi (scan terputus restart server, tak pernah tercatat gagal atau berhasil benar)."""
        sid = self.submitted_session(kelas)
        with SessionLocal() as db:
            s = db.get(ScanSession, sid)
            fid = s.files[0].id
            for sh in list(s.sheets):
                db.delete(sh)
            db.commit()
        return sid, fid

    def test_orphan_files_detected_and_shown_in_session_list_and_detail(self):
        sid, fid = self._make_orphan_file("ORPHAN-01")
        row = next(x for x in self.c.get("/api/admin/sessions?q=ORPHAN-01", headers=ADM).json()["items"] if x["id"] == sid)
        self.assertEqual(row["files"]["orphans"], 1)
        d = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()
        self.assertEqual(d["items"], [])
        self.assertEqual(len(d["orphan_files"]), 1)
        self.assertEqual(d["orphan_files"][0]["id"], fid)

    def test_reprocess_single_orphan_file_recovers_sheet(self):
        sid, fid = self._make_orphan_file("ORPHAN-02")
        r = self.c.post(f"/api/admin/files/{fid}/reprocess", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["queued"])
        t = time.time()
        while time.time() - t < 120 and self.c.get(f"/api/sessions/{sid}").json()["scanning"]:
            time.sleep(0.4)
        d = self.c.get(f"/api/sessions/{sid}").json()
        self.assertEqual(len(d["sheets"]), 1)   # lembar pulih tanpa perlu unggah ulang
        row = next(x for x in self.c.get("/api/admin/sessions?q=ORPHAN-02", headers=ADM).json()["items"] if x["id"] == sid)
        self.assertEqual(row["files"]["orphans"], 0)

    def test_reprocess_orphans_bulk_recovers_all(self):
        sid, _fid = self._make_orphan_file("ORPHAN-03")
        r = self.c.post(f"/api/admin/sessions/{sid}/reprocess-orphans", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["diproses_ulang"], 1)
        t = time.time()
        while time.time() - t < 120 and self.c.get(f"/api/sessions/{sid}").json()["scanning"]:
            time.sleep(0.4)
        self.assertEqual(len(self.c.get(f"/api/sessions/{sid}").json()["sheets"]), 1)
        # dipanggil lagi -> tak ada lagi yg orphan, aman (tak menggandakan)
        r2 = self.c.post(f"/api/admin/sessions/{sid}/reprocess-orphans", headers=ADM)
        self.assertEqual(r2.json()["diproses_ulang"], 0)

    def test_reprocess_file_requires_token_and_404s_unknown(self):
        self.assertEqual(self.c.post("/api/admin/files/tidak-ada/reprocess").status_code, 401)
        self.assertEqual(self.c.post("/api/admin/files/tidak-ada/reprocess", headers=ADM).status_code, 404)

    def _wait_rescan_done(self, sid, q, timeout=120):
        t = time.time()
        while time.time() - t < timeout:
            row = next((x for x in self.c.get("/api/admin/sessions", params={"q": q}, headers=ADM).json()["items"]
                        if x["id"] == sid), None)
            self.assertIsNotNone(row, "sesi tak ditemukan lewat pencarian q -- cek query di tes")
            if not row["rescanning"]:
                return
            time.sleep(0.4)
        self.fail("pindai-ulang massal tak selesai dlm waktu tunggu")

    def test_rescan_all_updates_existing_sheets_without_duplicating(self):
        # "Pindai ulang semua lembar" -- beda dgn reprocess-orphans (berkas TANPA sheet sama sekali):
        # ini utk lembar yg SUDAH punya hasil, jadi harus UPDATE di tempat, bukan menggandakan baris Sheet.
        sid = self.submitted_session("RESCANALL-01", n=2)
        self.submit(sid)
        self.c.post(f"/api/sessions/{sid}/validate-all", headers=ADM)
        before = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertEqual(len(before), 2)
        self.assertTrue(all(x["validated"] for x in before))
        r = self.c.post(f"/api/admin/sessions/{sid}/rescan-all", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["diantre"], r.json()["dilewati"]), (2, 0))
        self._wait_rescan_done(sid, "RESCANALL-01")
        after = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertEqual(len(after), 2)                        # tak menggandakan lembar
        self.assertEqual({x["id"] for x in before}, {x["id"] for x in after})   # baris yg sama, diupdate di tempat
        self.assertFalse(any(x["validated"] for x in after))   # validasi per-lembar direset, spt rescan tunggal

    def test_rescan_all_skips_sheets_whose_photo_was_cleared(self):
        sid = self.submitted_session("RESCANALL-02")
        self.c.post(f"/api/admin/sessions/{sid}/validate?value=true", headers=ADM)   # ikut men-submit & memvalidasi
        self.c.post(f"/api/admin/sessions/{sid}/clear-photos", headers=ADM)
        r = self.c.post(f"/api/admin/sessions/{sid}/rescan-all", headers=ADM)
        self.assertEqual((r.json()["diantre"], r.json()["dilewati"]), (0, 1))

    def test_rescan_all_requires_token_and_404s_unknown(self):
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/rescan-all").status_code, 401)
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/rescan-all", headers=ADM).status_code, 404)

    # ---------------------------------------------------------------- kalibrasi template
    def test_apply_field_calib_shifts_bubbles_and_roi_without_mutating_input(self):
        from scanner.service import apply_field_calib
        fields = {"X": {"roi": [10, 20, 100, 50], "items": [{"name": "X_1", "bubbles": [{"cx": 15.0, "cy": 25.0, "w": 4, "h": 4}]}]}}
        out = apply_field_calib(fields, {"X": (5, -3)})
        b = out["X"]["items"][0]["bubbles"][0]
        self.assertEqual((b["cx"], b["cy"]), (20.0, 22.0))
        self.assertEqual(out["X"]["roi"], [15, 17, 100, 50])
        self.assertEqual(fields["X"]["items"][0]["bubbles"][0]["cx"], 15.0)   # asli tak tersentuh

    def test_apply_field_calib_clamps_extreme_offset(self):
        from scanner.service import CALIB_MAX_OFFSET, apply_field_calib
        fields = {"X": {"items": [{"name": "X_1", "bubbles": [{"cx": 0.0, "cy": 0.0}]}]}}
        out = apply_field_calib(fields, {"X": (99999, -99999)})
        b = out["X"]["items"][0]["bubbles"][0]
        self.assertEqual((b["cx"], b["cy"]), (CALIB_MAX_OFFSET, -CALIB_MAX_OFFSET))

    def test_calib_fields_default_set_and_clear(self):
        d = self.c.get("/api/admin/calib/fields", headers=ADM).json()
        names = {f["name"] for f in d["fields"]}
        self.assertIn("NPM", names)
        npm = next(f for f in d["fields"] if f["name"] == "NPM")
        self.assertEqual((npm["dx"], npm["dy"], npm["updated_at"]), (0.0, 0.0, None))

        r = self.c.put("/api/admin/calib/fields/NPM", json={"dx": 12.5, "dy": -3}, headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["dx"], r.json()["dy"]), (12.5, -3.0))
        npm2 = next(f for f in self.c.get("/api/admin/calib/fields", headers=ADM).json()["fields"] if f["name"] == "NPM")
        self.assertEqual((npm2["dx"], npm2["dy"]), (12.5, -3.0))
        self.assertIsNotNone(npm2["updated_at"])

        self.assertEqual(self.c.delete("/api/admin/calib/fields/NPM", headers=ADM).status_code, 200)
        npm3 = next(f for f in self.c.get("/api/admin/calib/fields", headers=ADM).json()["fields"] if f["name"] == "NPM")
        self.assertEqual((npm3["dx"], npm3["dy"]), (0.0, 0.0))

    def test_calib_set_unknown_field_404_and_requires_token(self):
        self.assertEqual(self.c.put("/api/admin/calib/fields/TIDAK-ADA", json={"dx": 1, "dy": 1}, headers=ADM).status_code, 404)
        self.assertEqual(self.c.put("/api/admin/calib/fields/NPM", json={"dx": 1, "dy": 1}).status_code, 401)
        self.assertEqual(self.c.get("/api/admin/calib/fields").status_code, 401)

    def test_calib_reset_all(self):
        self.c.put("/api/admin/calib/fields/NPM", json={"dx": 5, "dy": 5}, headers=ADM)
        self.c.put("/api/admin/calib/fields/NAMA", json={"dx": 5, "dy": 5}, headers=ADM)
        r = self.c.post("/api/admin/calib/reset", headers=ADM)
        self.assertEqual(r.json()["direset"], 2)
        d = self.c.get("/api/admin/calib/fields", headers=ADM).json()
        self.assertTrue(all((f["dx"], f["dy"]) == (0.0, 0.0) for f in d["fields"]))

    def test_calib_upload_and_preview_roundtrip(self):
        r = self.c.post("/api/admin/calib/upload", headers=ADM, files={"file": ("l.pdf", PDF, "application/pdf")})
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["token"]
        self.assertEqual(r.json()["canvas"], {"width": 1700, "height": 2400})
        p1 = self.c.get(f"/api/admin/calib/preview?token={token}", headers=ADM)
        self.assertEqual((p1.status_code, p1.headers["content-type"]), (200, "image/jpeg"))
        p2 = self.c.get(f"/api/admin/calib/preview?token={token}&field=NPM&dx=10&dy=-5", headers=ADM)
        self.assertEqual(p2.status_code, 200)
        self.assertNotEqual(p1.content, p2.content)   # menonjolkan blok NPM benar2 mengubah gambar
        self.assertEqual(self.c.get("/api/admin/calib/preview?token=tidak-ada", headers=ADM).status_code, 410)
        self.assertEqual(self.c.post("/api/admin/calib/upload", files={"file": ("l.pdf", PDF, "application/pdf")}).status_code, 401)

    def test_calib_offset_changes_real_scan_result(self):
        # Bukti ujung-ke-ujung: koreksi kalibrasi bukan cuma tersimpan di DB, tapi BENAR2 dipakai worker
        # proses saat memindai sungguhan (lihat server.main._calib & scanner.service.apply_field_calib).
        base_sid = self.submitted_session("CALIBTEST-BASE")
        base_npm = self.c.get(f"/api/admin/sessions/{base_sid}/sheets", headers=ADM).json()["items"][0]["npm"]
        r = self.c.put("/api/admin/calib/fields/NPM", json={"dx": 0, "dy": 43}, headers=ADM)   # ~1 baris kotak
        self.assertEqual(r.status_code, 200, r.text)
        try:
            shift_sid = self.submitted_session("CALIBTEST-SHIFT")
            shift_npm = self.c.get(f"/api/admin/sessions/{shift_sid}/sheets", headers=ADM).json()["items"][0]["npm"]
        finally:
            self.c.delete("/api/admin/calib/fields/NPM", headers=ADM)
        self.assertNotEqual(base_npm, shift_npm)

    # ---------------------------------------------------------------- edit lembar & bulk-edit (admin)
    def test_admin_edit_sheet_updates_fields_and_jawaban_then_regrades(self):
        self.c.put("/api/admin/kunci/A", json={str(q): a for q, a in KUNCI["A"].items()}, headers=ADM)
        sid = self.submitted_session("ADMEDIT-01")
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)   # tandai validated dulu -> edit harus membatalkannya lagi

        r = self.c.patch(f"/api/admin/sheets/{shid}", json={"npm": "1234567890", "jawaban": {"1": "B", "02": "c"}}, headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d["npm"], "1234567890")
        self.assertFalse(d["validated"])   # data diedit -> validasi lembar ini dibatalkan
        self.assertEqual(d["record"]["soal_01"], "B")   # KUNCI["A"][1] == "B" -> benar
        self.assertEqual(d["record"]["soal_02"], "C")   # disimpan huruf besar
        self.assertNotIn(d["nilai"], ("-", ""))

        r2 = self.c.patch(f"/api/admin/sheets/{shid}", json={"npm": "123"}, headers=ADM)
        self.assertEqual(r2.status_code, 422, r2.text)   # NPM harus 10 digit
        r3 = self.c.patch(f"/api/admin/sheets/{shid}", json={"fakultas_ljk": "BUKAN FAKULTAS"}, headers=ADM)
        self.assertEqual(r3.status_code, 422, r3.text)

        self.assertEqual(self.c.patch(f"/api/admin/sheets/{shid}", json={"npm": "1234567890"}).status_code, 401)
        self.assertEqual(self.c.patch("/api/admin/sheets/tidak-ada", json={"npm": "1234567890"}, headers=ADM).status_code, 404)

    def test_admin_edit_sheet_updates_kuisioner_independent_of_jawaban(self):
        sid = self.submitted_session("ADMEDIT-KUI-01")
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        r = self.c.patch(f"/api/admin/sheets/{shid}", json={"kuisioner": {"1": "b", "02": "d"}}, headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["record"]["q01"], r.json()["record"]["q02"]), ("B", "D"))
        self.assertFalse(r.json()["validated"])

    def test_admin_edit_sheet_works_even_after_submit(self):
        sid = self.submitted_session("ADMEDIT-02")
        self.submit(sid)
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        r = self.c.patch(f"/api/admin/sheets/{shid}", json={"kode_soal": "999"}, headers=ADM)
        self.assertEqual((r.status_code, r.json()["kode_soal"]), (200, "999"))

    def test_admin_validate_single_sheet_works_even_after_submit(self):
        sid = self.submitted_session("ADMSHVAL-01")
        self.submit(sid)
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        r = self.c.post(f"/api/admin/sheets/{shid}/validate", headers=ADM)
        self.assertEqual((r.status_code, r.json()["validated"], r.json()["label"]), (200, True, "OK"))
        r2 = self.c.post(f"/api/admin/sheets/{shid}/validate?value=false", headers=ADM)
        self.assertEqual((r2.status_code, r2.json()["validated"]), (200, False))
        self.assertEqual(self.c.post(f"/api/admin/sheets/{shid}/validate").status_code, 401)
        self.assertEqual(self.c.post("/api/admin/sheets/tidak-ada/validate", headers=ADM).status_code, 404)

    def test_admin_sync_session_now_upserts_by_npm_without_duplicating(self):
        sid = self.submitted_session("ADMSYNC-01")
        self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)   # men-submit & memvalidasi
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        self.c.patch(f"/api/admin/sheets/{shid}", json={"npm": "1234567890"}, headers=ADM)
        self.assertEqual(len(self.fake.calls), 0)

        r = self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM)
        self.assertEqual((r.status_code, r.json()["appended"], r.json()["updated"]), (200, 1, 0))
        self.assertEqual(len(self.fake.rows_by_npm), 1)

        # admin mengoreksi data lagi (mis. kode soal) lalu kirim ULANG -> baris NPM yg SAMA diPERBARUI,
        # bukan ditambah lagi jadi baris baru (ini beda dari append_records polos yg dulu dipakai di sini).
        self.c.patch(f"/api/admin/sheets/{shid}", json={"kode_soal": "999"}, headers=ADM)
        r2 = self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM)
        self.assertEqual((r2.status_code, r2.json()["appended"], r2.json()["updated"]), (200, 0, 1))
        self.assertEqual(len(self.fake.rows_by_npm), 1)   # tetap 1 baris, bukan 2
        self.assertEqual(self.fake.rows_by_npm["1234567890"]["Kode Soal"], "999")

    def test_admin_sync_session_now_rejects_unsubmitted_and_reports_failure(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMSYNC-02"}).json()["id"]
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM).status_code, 409)

        sid2 = self.submitted_session("ADMSYNC-03")
        self.c.post(f"/api/admin/sessions/{sid2}/validate", headers=ADM)
        self.fake.fail = 1
        r = self.c.post(f"/api/admin/sessions/{sid2}/sync-now", headers=ADM)
        self.assertEqual(r.status_code, 409)
        self.assertIn("429", r.json()["detail"])

    # ---------------------------------------------------------------- akun admin (tab Akun)
    def test_admin_user_crud_lifecycle(self):
        r = self.c.post("/api/admin/users", json={"username": "tes_akun1", "password": "rahasia123", "name": "Tes Satu", "hp": "081111111111"}, headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        uid = r.json()["id"]
        self.assertTrue(r.json()["active"])

        rows = self.c.get("/api/admin/users", headers=ADM).json()
        row = next(x for x in rows if x["id"] == uid)
        self.assertEqual((row["username"], row["name"], row["hp"], row["created_by"]), ("tes_akun1", "Tes Satu", "081111111111", "ADMIN_TOKEN"))
        self.assertNotIn("password_hash", row)   # password tak pernah ikut terkirim balik

        r2 = self.c.patch(f"/api/admin/users/{uid}", json={"name": "Tes Diganti", "hp": "082222222222", "active": False}, headers=ADM)
        self.assertEqual((r2.status_code, r2.json()["name"], r2.json()["hp"], r2.json()["active"]), (200, "Tes Diganti", "082222222222", False))

        self.assertEqual(self.c.delete(f"/api/admin/users/{uid}", headers=ADM).status_code, 204)
        self.assertNotIn(uid, [x["id"] for x in self.c.get("/api/admin/users", headers=ADM).json()])
        self.assertEqual(self.c.patch(f"/api/admin/users/{uid}", json={"name": "x"}, headers=ADM).status_code, 404)

    def test_admin_user_inserted_via_raw_sql_with_bcrypt_hash_can_log_in(self):
        """Simulasi admin yg ditambah langsung lewat psql (bukan lewat tab Akun/endpoint POST /users) pakai
        pgcrypto: crypt(pw, gen_salt('bf')) -- hash-nya berformat bcrypt ("$2..."), BEDA dari format PBKDF2
        yg dihasilkan auth.hash_password(). auth._check_password harus bisa verifikasi keduanya."""
        auth._FAILS.clear()
        import bcrypt
        bcrypt_hash = bcrypt.hashpw("rahasiaBcrypt1".encode(), bcrypt.gensalt()).decode()
        with SessionLocal() as db:
            db.add(AdminUser(username="tes_bcrypt1", password_hash=bcrypt_hash, name="Tes Bcrypt", hp="083333333333",
                              active=True, created_by="psql"))
            db.commit()
        r = self.c.post("/auth/password", json={"username": "tes_bcrypt1", "password": "rahasiaBcrypt1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.c.post("/auth/logout")   # lepas cookie sesi -- self.c dipakai BERSAMA semua tes di kelas ini (setUpClass)
        self.assertEqual(self.c.post("/auth/password", json={"username": "tes_bcrypt1", "password": "salah"}).status_code, 401)
        row = next(x for x in self.c.get("/api/admin/users", headers=ADM).json() if x["username"] == "tes_bcrypt1")
        self.assertEqual(row["hp"], "083333333333")

    def test_admin_create_user_rejects_weak_password_duplicate_and_bad_username(self):
        self.assertEqual(self.c.post("/api/admin/users", json={"username": "ab", "password": "rahasia123"}, headers=ADM).status_code, 422)
        self.assertEqual(self.c.post("/api/admin/users", json={"username": "tes_akun2", "password": "pendek"}, headers=ADM).status_code, 422)
        r = self.c.post("/api/admin/users", json={"username": "tes_akun3", "password": "rahasia123"}, headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.post("/api/admin/users", json={"username": "tes_akun3", "password": "lainnya123"}, headers=ADM).status_code, 409)
        self.assertEqual(self.c.post("/api/admin/users", json={"username": "tes_akun3", "password": "rahasia123"}).status_code, 401)

    def test_new_admin_user_can_log_in_and_is_recorded_as_validator(self):
        # auth._FAILS (pembatas 5x percobaan/15mnt) kunci per-IP, tapi TestClient Starlette selalu melapor
        # IP palsu yg SAMA ("testclient") -- jadi dipakai BERSAMA oleh tes lain di modul/berkas manapun dlm
        # satu sesi pytest (mis. test_auth.py sengaja memicu lockout). Bersihkan dulu spy tes ini tak
        # bergantung urutan jalannya tes lain.
        auth._FAILS.clear()
        self.c.post("/api/admin/users", json={"username": "tes_login1", "password": "rahasia123", "name": "Pak Login"}, headers=ADM)
        r = self.c.post("/auth/password", json={"username": "tes_login1", "password": "rahasia123"})
        self.assertEqual(r.status_code, 200, r.text)
        me = self.c.get("/auth/me").json()
        self.assertEqual((me["authed"], me["email"], me["name"]), (True, "tes_login1", "Pak Login"))

        sid = self.submitted_session("ADMUSERVAL-01")
        rv = self.c.post(f"/api/admin/sessions/{sid}/validate")   # pakai cookie sesi, bukan token
        self.assertEqual(rv.status_code, 200, rv.text)
        self.assertEqual(rv.json()["admin_validated_by"], "Pak Login")
        self.c.post("/auth/logout")

        # password salah -> ditolak; akun dinonaktifkan -> ikut ditolak
        self.assertEqual(self.c.post("/auth/password", json={"username": "tes_login1", "password": "salah-banget"}).status_code, 401)
        uid = next(x["id"] for x in self.c.get("/api/admin/users", headers=ADM).json() if x["username"] == "tes_login1")
        self.c.patch(f"/api/admin/users/{uid}", json={"active": False}, headers=ADM)
        self.assertEqual(self.c.post("/auth/password", json={"username": "tes_login1", "password": "rahasia123"}).status_code, 401)

    def test_admin_bulk_edit_applies_kode_soal_and_fakultas_to_every_sheet(self):
        sid = self.submitted_session("ADMBULK-01", n=3)
        items_before = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertEqual(len(items_before), 3)
        r = self.c.post(f"/api/admin/sessions/{sid}/bulk-edit", json={"kode_soal": "042", "fakultas_ljk": "FIF"}, headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["diubah"], 3)
        items_after = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertTrue(all(x["kode_soal"] == "042" for x in items_after))
        self.assertTrue(all(x["fakultas_ljk"] == "FIF" for x in items_after))
        self.assertTrue(all(not x["validated"] for x in items_after))   # diedit -> perlu dicek ulang

    def test_admin_bulk_edit_requires_at_least_one_field_and_token(self):
        sid = self.submitted_session("ADMBULK-02")
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/bulk-edit", json={}, headers=ADM).status_code, 422)
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/bulk-edit", json={"kode_soal": "1"}).status_code, 401)
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/bulk-edit", json={"kode_soal": "1"}, headers=ADM).status_code, 404)

    def test_admin_unvalidate_all_sheets_clears_every_sheet_without_touching_session_submitted(self):
        sid = self.submitted_session("ADMUNVAL-01", n=3)
        self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)
        items = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertTrue(all(x["validated"] for x in items))

        r = self.c.post(f"/api/admin/sessions/{sid}/unvalidate-all-sheets", headers=ADM)
        self.assertEqual((r.status_code, r.json()["dibatalkan"]), (200, 3))
        items2 = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertTrue(all(not x["validated"] for x in items2))
        # beda dgn admin_validate_session(value=False): sesi TETAP submitted (bukan "batalkan validasi
        # sesi", cuma per-lembar) -- bandingkan dgn docstring admin_unvalidate_all_sheets di server/admin.py
        self.assertTrue(self.c.get(f"/api/sessions/{sid}").json()["submitted"])

        # dipanggil lagi saat semua sudah tak tervalidasi -> tak ada yg "dibatalkan" lagi (bukan error)
        r2 = self.c.post(f"/api/admin/sessions/{sid}/unvalidate-all-sheets", headers=ADM)
        self.assertEqual(r2.json()["dibatalkan"], 0)
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/unvalidate-all-sheets").status_code, 401)
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/unvalidate-all-sheets", headers=ADM).status_code, 404)

    # ---------------------------------------------------------------- riwayat akses admin
    def test_access_log_records_password_login_success_and_failure(self):
        auth._FAILS.clear()   # lihat catatan di test_new_admin_user_can_log_in... -- _FAILS dibagi lintas tes
        self.c.post("/api/admin/users", json={"username": "tes_akseslog1", "password": "rahasia123"}, headers=ADM)
        before = len(self.c.get("/api/admin/access-log", headers=ADM).json())
        self.c.post("/auth/password", json={"username": "tes_akseslog1", "password": "salah-sekali"})
        r = self.c.post("/auth/password", json={"username": "tes_akseslog1", "password": "rahasia123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.c.post("/auth/logout")
        log = self.c.get("/api/admin/access-log", headers=ADM).json()
        self.assertGreaterEqual(len(log), before + 2)   # 1 gagal + 1 berhasil tercatat
        by_this_user = [x for x in log if x["identity"] == "tes_akseslog1"]
        self.assertTrue(any(not x["success"] and x["method"] == "password" for x in by_this_user))
        self.assertTrue(any(x["success"] and x["method"] == "password" for x in by_this_user))
        self.assertEqual(self.c.get("/api/admin/access-log").status_code, 401)


if __name__ == "__main__":
    unittest.main()
