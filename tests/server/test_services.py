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
from server import db as dbmod
from server.db import AdminUser, ScanSession, Sheet, SessionLocal, TemplateCalib, UploadFile
from server.main import app
from tests.regression.fixtures import KUNCI

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ADM = {"X-Admin-Token": "rahasia"}
VALID = {"nama_pengawas": "Budi Santoso", "hp": "081234567890", "ruangan": "TULT 0603", "kelas": "BS1SI-50-REG-01",
         "prodi": "S1 Sistem Informasi", "fakultas": "FIF", "hari_ujian": "2026-09-28", "kode_soal": "A"}
PDF = open(os.path.join(ROOT, "LJK.pdf"), "rb").read()
JPEG = open(os.path.join(ROOT, "sample foto", "dari pak bagas.jpeg"), "rb").read()


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
            self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/validate?value=true", headers=ADM).status_code, 200)
            self.c.post("/api/sessions", json={**VALID, "kelas": "MON-B"})
            # MON-C: submit TAPI sengaja TAK divalidasi admin -- masih "berjalan"/Checking. Dipakai utk
            # membuktikan mhs_upload (total unggahan, lepas status) & mhs_validated (cuma yg divalidasi)
            # benar2 kehitung terpisah: upload-nya harus ikut kehitung di sini walau belum divalidasi.
            sid_c = self.submitted_session("MON-C", n=1)
            self.submit(sid_c)
            extra = self.submitted_session("LUAR-JADWAL", n=1)
            self.submit(extra)
            d = self.c.get("/api/admin/monitor?refresh=true", headers=ADM).json()
            self.assertEqual(d["overall"]["total"], 3)                      # baris Online tidak dihitung
            self.assertEqual(d["overall"]["selesai"], 1)                    # MON-A: submit + DIVALIDASI admin
            self.assertEqual(d["overall"]["berjalan"], 2)                   # MON-B (blm submit), MON-C (submit tp blm divalidasi)
            senin = next(x for x in d["days"] if x["hari"] == "SENIN")
            a = next(x for x in senin["slots"] if x["kelas"] == "MON-A")
            self.assertEqual((a["status"], a["lembar"], a["jml_mhs"]), ("selesai", 2, 3))
            self.assertEqual(senin["mhs_upload"], 2)
            self.assertEqual(senin["mhs_validated"], 2)                     # SENIN cuma ada MON-A yg divalidasi
            self.assertEqual(senin["pct_mhs"], round(2 / 5 * 100, 1))
            selasa = next(x for x in d["days"] if x["hari"] == "SELASA")
            c = next(x for x in selasa["slots"] if x["kelas"] == "MON-C")
            self.assertEqual((c["status"], c["lembar"], c["jml_mhs"]), ("berjalan", 1, 4))
            self.assertEqual(selasa["mhs_upload"], 1)        # MON-C ikut kehitung di total upload...
            self.assertEqual(selasa["mhs_validated"], 0)     # ...tapi TIDAK di validated (blm divalidasi admin)
            self.assertEqual(d["overall"]["mhs_upload"], 3)      # MON-A(2) + MON-C(1)
            self.assertEqual(d["overall"]["mhs_validated"], 2)   # cuma MON-A
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
        self.assertEqual((r.status_code, r.json()["admin_validated"], r.json()["submitted"]), (200, True, False))
        self.assertEqual(r.json()["admin_validated_by"], "ADMIN_TOKEN")   # identitas admin yg menandai (lihat auth.require_admin)
        # "validated" & "sent" SENGAJA dipisah (2 Okt 2026): lembar yg berhasil discan otomatis tervalidasi,
        # TAPI sesi belum terkunci/terkirim -- itu baru terjadi kalau admin SENGAJA menekan "Kirim" (sync-now).
        d = self.c.get(f"/api/sessions/{sid}").json()
        self.assertFalse(d["submitted"])
        self.assertTrue(all(x["validated"] for x in d["sheets"]))
        rows = self.c.get("/api/admin/sessions?status=validated", headers=ADM).json()["items"]
        self.assertIn(sid, [x["id"] for x in rows])
        self.assertEqual(next(x for x in rows if x["id"] == sid)["admin_validated_by"], "ADMIN_TOKEN")
        self.assertNotIn(sid, [x["id"] for x in self.c.get("/api/admin/sessions?status=perlu_cek", headers=ADM).json()["items"]])
        # admin menekan "Kirim" (sync-now) -- BARU di sini sesi terkunci (submitted) & benar2 terkirim
        r = self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.c.get(f"/api/sessions/{sid}").json()["submitted"])
        # batalkan tanda validated -- tak menyentuh submitted (sinkron sudah berjalan; lihat docstring), tapi
        # "divalidasi oleh" ikut dikosongkan (bukan validated lagi, jadi tak relevan menyebut siapa yg dulu menandai)
        r = self.c.post(f"/api/admin/sessions/{sid}/validate?value=false", headers=ADM)
        self.assertEqual((r.status_code, r.json()["admin_validated"], r.json()["submitted"]), (200, False, True))
        self.assertIsNone(r.json()["admin_validated_by"])

    def test_admin_sessions_sort_by_column_across_pages(self):
        for k in ("SORTK-B", "SORTK-C", "SORTK-A"):
            self.c.post("/api/sessions", json={**VALID, "kelas": k})
        asc = self.c.get("/api/admin/sessions?q=SORTK-&sort=kelas&dir=asc", headers=ADM).json()["items"]
        desc = self.c.get("/api/admin/sessions?q=SORTK-&sort=kelas&dir=desc", headers=ADM).json()["items"]
        self.assertEqual([x["kelas"] for x in asc], ["SORTK-A", "SORTK-B", "SORTK-C"])
        self.assertEqual([x["kelas"] for x in desc], ["SORTK-C", "SORTK-B", "SORTK-A"])
        # urut dilakukan SEBELUM paginasi: halaman 1 berukuran 1 harus berisi yg terkecil, bukan sekadar baris pertama
        p1 = self.c.get("/api/admin/sessions?q=SORTK-&sort=kelas&dir=asc&size=1&page=1", headers=ADM).json()
        self.assertEqual((p1["total"], [x["kelas"] for x in p1["items"]]), (3, ["SORTK-A"]))
        # kolom turunan (lembar) & kunci tak dikenal tak membuat error
        self.assertEqual(self.c.get("/api/admin/sessions?q=SORTK-&sort=lembar&dir=desc", headers=ADM).status_code, 200)
        self.assertEqual(self.c.get("/api/admin/sessions?q=SORTK-&sort=bukan_kolom", headers=ADM).status_code, 200)

    def test_create_session_always_replaces_old_unprotected_sessions(self):
        """Membuat sesi baru utk kelas yg sudah ada SELALU menimpa sesi lama yg belum dikunci (apa pun
        nilai replace_existing -- lapangan itu kini vestigial, lihat create_session), beserta foto &
        lembarnya -- supaya kelas yg sama tak pernah punya lebih dari satu sesi hidup sekaligus (dulu bug:
        tanpa replace_existing, create_session tetap membuat sesi BARU tanpa menghapus yg lama sama sekali,
        jadi satu kelas bisa numpuk banyak sesi dobel). Sesi yg sudah divalidasi admin / terkirim DITOLAK
        (409) -- hanya admin yg boleh menyentuhnya."""
        kelas = "REPL-01"
        old = self.submitted_session(kelas)                       # belum divalidasi admin -> boleh ditimpa
        with SessionLocal() as db:
            path = db.get(ScanSession, old).files[0].path
        self.assertTrue(os.path.exists(path))
        r = self.c.post("/api/sessions", json={**VALID, "kelas": kelas})
        self.assertEqual((r.status_code, r.json()["diganti"]), (201, 1))
        self.assertEqual(self.c.get(f"/api/sessions/{old}").status_code, 404)
        self.assertFalse(os.path.exists(path))
        # sesi tervalidasi admin: upload baru utk kelas itu DITOLAK, bukan diam2 menimpa / numpuk dobel
        kept = self.submitted_session("REPL-02")
        self.c.post(f"/api/admin/sessions/{kept}/validate", headers=ADM)
        r2 = self.c.post("/api/sessions", json={**VALID, "kelas": "REPL-02"})
        self.assertEqual(r2.status_code, 409, r2.text)
        self.assertEqual(self.c.get(f"/api/sessions/{kept}").status_code, 200)
        self.assertEqual(self.c.get("/api/admin/sessions?q=REPL-02", headers=ADM).json()["total"], 1)

    def test_admin_sessions_filters_by_hari_ujian(self):
        a = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMHARI-A", "hari_ujian": "2026-09-29"}).json()["id"]
        b = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMHARI-B", "hari_ujian": "2026-09-30"}).json()["id"]
        rows = self.c.get("/api/admin/sessions?hari=2026-09-29", headers=ADM).json()["items"]
        ids = {x["id"] for x in rows}
        self.assertIn(a, ids)
        self.assertNotIn(b, ids)
        rows_all = self.c.get("/api/admin/sessions", headers=ADM).json()["items"]
        self.assertTrue({a, b}.issubset({x["id"] for x in rows_all}))   # tanpa filter: semua hari tampil

    def test_admin_sessions_lembar_counts_uploaded_files_not_scanned_sheets(self):
        # "Lembar" di tabel Sesi = jumlah FOTO terupload (UploadFile), bukan jumlah lembar hasil scan
        # (Sheet) -- 1 PDF rusak/gagal tetap kehitung sbg 1 foto terupload walau nol Sheet dihasilkan.
        sid = self.submitted_session("ADMLEMBAR-01", n=2)
        row = next(x for x in self.c.get("/api/admin/sessions?q=ADMLEMBAR-01", headers=ADM).json()["items"] if x["id"] == sid)
        self.assertEqual(row["files"]["total"], 2)
        self.assertEqual(row["lembar"], 2)   # backend tetap simpan jumlah lembar hasil scan (dipakai internal)

    def test_admin_validate_session_requires_at_least_one_sheet(self):
        sid = self.c.post("/api/sessions", json={**VALID, "kelas": "ADMV-EMPTY"}).json()["id"]
        r = self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)
        self.assertEqual(r.status_code, 409, r.text)

    def test_admin_sync_now_requires_validated_first(self):
        """"Kirim" (sync-now) cuma boleh diklik admin SETELAH sesi ditandai validated -- "validated" &
        "sent" sengaja dipisah (lihat admin_validate_session/admin_sync_session_now), jadi tak ada sesi
        yg nyasar terkirim ke Google Sheet produksi sebelum admin benar2 menekan "Kirim"."""
        sid = self.submitted_session("ADMSENTGATE-01")
        r = self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("validated", r.json()["detail"].lower())
        self.assertFalse(self.c.get(f"/api/sessions/{sid}").json()["submitted"])
        self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)
        r2 = self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertTrue(self.c.get(f"/api/sessions/{sid}").json()["submitted"])

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

    def test_public_monitor_sesi_upload_pct_is_kelas_based(self):
        from server import plotting
        csv_text = ("No,Hari,Jam Mulai,Jam Selesai,Gedung,Ruangan,Kelas,Prodi,Jml Mahasiswa,Nama Pengawas,Cek Bentrok,Mode,\n"
                    "1,SENIN,08:30,09:30,KU1,R1,PUBMON-A,S1 X,1,Budi,OK,Onsite,\n"
                    "2,SENIN,09:30,10:30,KU1,R2,PUBMON-B,S1 X,2,Ani,OK,Onsite,\n")
        old_download = plotting._download
        plotting._download = lambda: csv_text
        plotting._cache.update(rows=None, at=0.0, error=None)
        try:
            # PUBMON-A sudah ada unggahan (berapa pun lembarnya, tetap kehitung SATU kelas) -- PUBMON-B
            # belum sama sekali. Persentase harus berbasis JUMLAH KELAS (1 dari 2 = 50%), bukan lembar/mhs.
            sid = self.c.post("/api/sessions", json={**VALID, "kelas": "PUBMON-A"}).json()["id"]
            self.c.post(f"/api/sessions/{sid}/files", files=[("files", (f"l{i}.pdf", PDF, "application/pdf")) for i in range(2)])
            d = self.c.get("/api/monitor-sesi").json()
            self.assertEqual(d["kelas_total"], 2)
            self.assertEqual(d["kelas_upload"], 1)
            self.assertEqual(d["upload_pct"], 50.0)
            self.assertEqual(next(x for x in d["items"] if x["kelas"] == "PUBMON-A")["jml_mhs"], 1)   # kolom Mahasiswa di /monitor (dari jadwal)
        finally:
            plotting._download = old_download
            plotting._cache.update(rows=None, at=0.0, error=None)

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
        # Hapus satu sesi tak boleh ikut menghapus foto sesi lain di kelas yg sama. Sejak create_session
        # SELALU menimpa sesi lama kelas yg sama (satu kelas = satu sesi hidup, lihat
        # test_create_session_always_replaces_old_unprotected_sessions), dua sesi hidup sekelas tak lagi
        # bisa dibuat lewat endpoint publik -- jadi di sini keduanya disisipkan langsung ke DB (spt data
        # lama dari sblm aturan itu ada) murni utk menguji isolasi hapus-berkas di folder yg dibagi.
        with SessionLocal() as db:
            sa = ScanSession(nama_pengawas=VALID["nama_pengawas"], hp="+6281234567890", ruangan="",
                              kelas="ADMDEL-SHARED", fakultas=VALID["fakultas"], prodi=VALID["prodi"])
            sb = ScanSession(nama_pengawas=VALID["nama_pengawas"], hp="+6281234567890", ruangan="",
                              kelas="ADMDEL-SHARED", fakultas=VALID["fakultas"], prodi=VALID["prodi"])
            db.add_all([sa, sb])
            db.commit()
            a, b = sa.id, sb.id
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

    def test_admin_add_sheet_photo_creates_new_sheet_and_counts_toward_lembar(self):
        """Tambah lembar baru dari foto yg diunggah admin (mis. pengawas lupa unggah satu lembar) --
        jumlah "lembar" di tabel Sesi & admin/sessions/{sid}/sheets harus ikut bertambah."""
        sid = self.submitted_session("ADMADD-01", n=1)
        self.submit(sid)   # bekerja walau sesi sudah disubmit, spt replace foto (admin-only)
        before = len(self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"])
        r = self.c.post(f"/api/admin/sessions/{sid}/sheets/add", headers=ADM, files={"file": ("tambahan.jpeg", JPEG, "image/jpeg")})
        self.assertEqual(r.status_code, 200, r.text)
        added = r.json()["lembar"]
        self.assertGreaterEqual(added, 1)
        after_items = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertEqual(len(after_items), before + added)
        row = next(x for x in self.c.get("/api/admin/sessions?q=ADMADD-01", headers=ADM).json()["items"] if x["id"] == sid)
        self.assertEqual(row["lembar"], len(after_items))
        self.assertEqual(row["files"]["total"], 2)   # 1 dari submitted_session + 1 dari tambahan ini

    def test_scan_result_image_is_saved_at_scan_time_and_refreshed_on_rescan(self):
        """Gambar 'Hasil scan' DISIMPAN saat pemindaian (awal, pindai ulang, ganti foto) -- admin membukanya
        langsung tanpa memindai ulang (main._store_overlay). Cache dihapus saat lembar dihapus / sesi 'Sent'."""
        from server.main import _scan_cache_path
        sid = self.submitted_session("ADMCACHE-01")
        item = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]
        shid, cache_path = item["id"], _scan_cache_path(item["id"])

        self.assertTrue(os.path.exists(cache_path))               # sudah ada begitu selesai discan, tanpa perlu dibuka dulu
        self.assertTrue(item["scan_cached"])
        pv = self.c.get(f"/api/sheets/{shid}/preview")
        self.assertEqual((pv.status_code, pv.headers["content-type"]), (200, "image/jpeg"))
        saved = open(cache_path, "rb").read()
        self.assertEqual(pv.content, saved)                       # yang disajikan = berkas tersimpan

        os.remove(cache_path)                                     # cache hilang -> dibuat ulang saat diminta
        self.assertEqual(self.c.get(f"/api/sheets/{shid}/preview").status_code, 200)
        self.assertTrue(os.path.exists(cache_path))
        os.utime(cache_path, (1, 1))                              # tandai lama -> ?refresh=1 harus menulis ulang
        self.c.get(f"/api/sheets/{shid}/preview?refresh=1")
        self.assertGreater(os.path.getmtime(cache_path), 100)

        os.remove(cache_path)
        self.c.post(f"/api/admin/sheets/{shid}/rescan", headers=ADM)
        self.assertTrue(os.path.exists(cache_path))               # pindai ulang menyimpan gambar baru
        os.remove(cache_path)
        self.c.post(f"/api/admin/sheets/{shid}/replace", headers=ADM, files={"file": ("baru.pdf", PDF, "application/pdf")})
        self.assertTrue(os.path.exists(cache_path))               # ganti foto juga

        self.assertEqual(self.c.delete(f"/api/admin/sheets/{shid}", headers=ADM).status_code, 204)
        self.assertFalse(os.path.exists(cache_path))              # lembar dihapus -> cache ikut dibersihkan

    def test_sync_now_clears_scan_cache_once_sent(self):
        sid = self.submitted_session("ADMCACHE-02")
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        self.submit(sid)
        self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)   # "Kirim" sekarang mensyaratkan validated dulu
        from server.main import _scan_cache_path
        cache_path = _scan_cache_path(shid)
        self.c.get(f"/api/sheets/{shid}/preview")
        self.assertTrue(os.path.exists(cache_path))
        r = self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(os.path.exists(cache_path))           # sesi 'Sent' -> cache 'Hasil scan' tak lagi diperlukan

    def test_admin_delete_sheet_removes_it_even_after_submit(self):
        sid = self.submitted_session("ADMDELSHEET-01")
        self.submit(sid)
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        self.assertEqual(self.c.delete(f"/api/admin/sheets/{shid}").status_code, 401)   # tanpa token
        self.assertEqual(self.c.delete("/api/admin/sheets/tidak-ada", headers=ADM).status_code, 404)
        r = self.c.delete(f"/api/admin/sheets/{shid}", headers=ADM)
        self.assertEqual(r.status_code, 204, r.text)
        self.assertEqual(self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"], [])

    def test_admin_delete_selected_sheets_removes_only_checked_ones(self):
        sid = self.submitted_session("ADMDELSEL-01", n=3)
        self.submit(sid)
        items = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertEqual(len(items), 3)
        keep_id = items[0]["id"]
        delete_ids = [items[1]["id"], items[2]["id"]]
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/delete-selected", json={"sheet_ids": delete_ids}).status_code, 401)
        r = self.c.post(f"/api/admin/sessions/{sid}/delete-selected", headers=ADM, json={"sheet_ids": delete_ids})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["dihapus"], 2)
        remaining = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertEqual([x["id"] for x in remaining], [keep_id])
        # id tak dikenal diabaikan dgn tenang (bukan 404) -- batch, sebagian bisa saja sudah lenyap
        r2 = self.c.post(f"/api/admin/sessions/{sid}/delete-selected", headers=ADM, json={"sheet_ids": ["tidak-ada"]})
        self.assertEqual((r2.status_code, r2.json()["dihapus"]), (200, 0))

    def test_admin_delete_sheet_removes_photo_without_leaving_an_orphan(self):
        """Hapus lembar HARUS ikut menghapus foto sumbernya dari disk, & TIDAK boleh meninggalkan
        UploadFile state='done' tanpa Sheet (persis kondisi yg dideteksi _orphan_files sbg "hilang
        senyap") -- lihat main._delete_sheet_and_cleanup."""
        sid = self.submitted_session("ADMDELPHOTO-01")
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        with SessionLocal() as db:
            path = db.get(UploadFile, db.get(Sheet, shid).file_id).path
        self.assertTrue(os.path.exists(path))
        r = self.c.delete(f"/api/admin/sheets/{shid}", headers=ADM)
        self.assertEqual(r.status_code, 204, r.text)
        self.assertFalse(os.path.exists(path))   # foto ikut terhapus
        self.assertEqual(self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["orphan_files"], [])
        row = next(x for x in self.c.get("/api/admin/sessions?q=ADMDELPHOTO-01", headers=ADM).json()["items"] if x["id"] == sid)
        self.assertEqual(row["files"]["orphans"], 0)

    def test_admin_delete_sheet_keeps_shared_photo_until_its_last_sheet_is_gone(self):
        """Satu UploadFile bisa dipakai beberapa Sheet (PDF multi-halaman, lihat Sheet.file_id) -- hapus
        SATU lembar yg masih berbagi berkas dgn lembar lain TAK boleh ikut menghapus fotonya; baru dihapus
        begitu lembar TERAKHIR yg memakainya ikut dihapus."""
        sid = self.submitted_session("ADMDELSHARED-01")
        with SessionLocal() as db:
            s = db.get(ScanSession, sid)
            sh1 = s.sheets[0]
            sh1_id, file_id = sh1.id, sh1.file_id
            path = db.get(UploadFile, file_id).path
            sh2 = Sheet(session_id=sid, file_id=file_id, page=1, seq=2, doc_name="hal2",
                        scan_status=sh1.scan_status, record=dict(sh1.record))
            db.add(sh2)
            db.commit()
            sh2_id = sh2.id
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.c.delete(f"/api/admin/sheets/{sh1_id}", headers=ADM).status_code, 204)
        self.assertTrue(os.path.exists(path))        # sh2 masih memakainya -- jangan ikut terhapus
        with SessionLocal() as db:
            self.assertIsNotNone(db.get(UploadFile, file_id))
        self.assertEqual(self.c.delete(f"/api/admin/sheets/{sh2_id}", headers=ADM).status_code, 204)
        self.assertFalse(os.path.exists(path))        # lembar terakhir yg memakainya -> baru dihapus
        with SessionLocal() as db:
            self.assertIsNone(db.get(UploadFile, file_id))

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
        self.c.post(f"/api/admin/sessions/{sid}/validate?value=true", headers=ADM)   # status jadi "validated"
        self.c.post(f"/api/admin/sessions/{sid}/clear-photos", headers=ADM)
        r = self.c.post(f"/api/admin/sessions/{sid}/rescan-all", headers=ADM)
        self.assertEqual((r.json()["diantre"], r.json()["dilewati"]), (0, 1))

    def test_rescan_all_requires_token_and_404s_unknown(self):
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/rescan-all").status_code, 401)
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/rescan-all", headers=ADM).status_code, 404)

    def test_rescan_selected_only_queues_checked_sheets(self):
        # "Pindai ulang terpilih" di popup Detail lembar -- beda dari rescan-all (SEMUA lembar), ini cuma
        # yg dicentang admin. Kirim SATU dari dua id lembar, pastikan cuma itu yg diantre & diupdate.
        sid = self.submitted_session("RESCANSEL-01", n=2)
        self.submit(sid)
        self.c.post(f"/api/sessions/{sid}/validate-all", headers=ADM)
        items = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertEqual(len(items), 2)
        target = items[0]
        r = self.c.post(f"/api/admin/sessions/{sid}/rescan-selected", json={"sheet_ids": [target["id"]]}, headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["diantre"], r.json()["dilewati"]), (1, 0))
        self._wait_rescan_done(sid, "RESCANSEL-01")
        after = {x["id"]: x for x in self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]}
        self.assertFalse(after[target["id"]]["validated"])          # yg diantre: validasi per-lembar direset
        self.assertTrue(after[items[1]["id"]]["validated"])         # yg TAK dicentang: tak tersentuh sama sekali

    def test_rescan_selected_requires_token_and_404s_unknown(self):
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/rescan-selected", json={"sheet_ids": []}).status_code, 401)
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/rescan-selected", json={"sheet_ids": []}, headers=ADM).status_code, 404)

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
        self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)   # "Kirim" (sync-now) mensyaratkan ini dulu
        shid = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"][0]["id"]
        self.c.patch(f"/api/admin/sheets/{shid}", json={"npm": "1234567890"}, headers=ADM)
        self.assertEqual(len(self.fake.calls), 0)

        r = self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM)   # men-submit (blm pernah) lalu sinkron
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

    def test_seed_env_admin_accounts_copies_hash_verbatim_and_is_idempotent(self):
        """db._seed_env_admin_accounts() -- migrasi akun ADMIN_USER/ADMIN_ACCOUNTS yg masih hardcode di env
        (deploy/.env) ke tabel admin_user, supaya ke depannya dikelola tanpa redeploy. Hash disalin APA
        ADANYA (bukan dihitung ulang dari password asli -- toh kita tak pernah tahu plaintext-nya)."""
        saved = (config.ADMIN_USER, config.ADMIN_PASSWORD_HASH, config.ADMIN_ACCOUNTS)
        config.ADMIN_USER, config.ADMIN_PASSWORD_HASH = "tes_seed_env", "240000:deadbeef:cafef00d"
        config.ADMIN_ACCOUNTS = "tes_seed_env2:240000:aaaa:bbbb"
        try:
            dbmod._seed_env_admin_accounts()
            with SessionLocal() as sdb:
                rows = {u.username: u for u in sdb.query(AdminUser).filter(AdminUser.username.in_(["tes_seed_env", "tes_seed_env2"]))}
            self.assertEqual(set(rows), {"tes_seed_env", "tes_seed_env2"})
            self.assertEqual(rows["tes_seed_env"].password_hash, "240000:deadbeef:cafef00d")   # disalin APA ADANYA
            self.assertEqual(rows["tes_seed_env"].created_by, "migrasi otomatis dari .env")
            # jalan lagi (spt tiap kali server restart) -- TAK boleh menggandakan atau menimpa baris yg sudah ada
            with SessionLocal() as sdb:
                row = sdb.query(AdminUser).filter_by(username="tes_seed_env").one()
                row.password_hash = "sudah-diubah-admin-lewat-tab-akun"
                sdb.commit()
            dbmod._seed_env_admin_accounts()
            with SessionLocal() as sdb:
                self.assertEqual(sdb.query(AdminUser).filter_by(username="tes_seed_env").count(), 1)
                self.assertEqual(sdb.query(AdminUser).filter_by(username="tes_seed_env").one().password_hash, "sudah-diubah-admin-lewat-tab-akun")
        finally:
            config.ADMIN_USER, config.ADMIN_PASSWORD_HASH, config.ADMIN_ACCOUNTS = saved
            with SessionLocal() as sdb:
                sdb.query(AdminUser).filter(AdminUser.username.in_(["tes_seed_env", "tes_seed_env2"])).delete(synchronize_session=False)
                sdb.commit()

    def test_seed_pengawas_from_tsv_is_idempotent_and_derives_dosen_from_empty_nim(self):
        """db._seed_pengawas_from_tsv() -- migrasi SEKALI jalan dari berkas pengawas.tsv lama ke tabel
        pengawas (server/db.py Pengawas), supaya admin selanjutnya tambah/ubah pengawas lewat psql, bukan
        edit berkas + redeploy. id baris migrasi pakai hash nama+nim (skema id lama) spy idempoten."""
        import hashlib
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8")
        f.write("Nama Pengawas\tNIM\tNo HP\nTes Seed Mhs\t900001\t6281111111111\nTes Seed Dosen\t\t\n")
        f.close()
        old = os.environ.get("PENGAWAS_FILE")
        os.environ["PENGAWAS_FILE"] = f.name
        pid_mhs = hashlib.sha1(b"Tes Seed Mhs900001").hexdigest()[:10]
        pid_dosen = hashlib.sha1(b"Tes Seed Dosen").hexdigest()[:10]
        try:
            dbmod._seed_pengawas_from_tsv()
            with SessionLocal() as sdb:
                mhs = sdb.get(dbmod.Pengawas, pid_mhs)
                dosen = sdb.get(dbmod.Pengawas, pid_dosen)
            self.assertEqual((mhs.nama, mhs.hp), ("Tes Seed Mhs", "+6281111111111"))
            self.assertEqual((dosen.nama, dosen.nim), ("Tes Seed Dosen", ""))
            # jalan lagi (spt tiap startup) -- TAK boleh menggandakan atau menimpa baris yg sudah diedit admin
            with SessionLocal() as sdb:
                row = sdb.get(dbmod.Pengawas, pid_mhs)
                row.hp = "sudah-diubah-admin-lewat-psql"
                sdb.commit()
            dbmod._seed_pengawas_from_tsv()
            with SessionLocal() as sdb:
                self.assertEqual(sdb.query(dbmod.Pengawas).filter_by(id=pid_mhs).count(), 1)
                self.assertEqual(sdb.get(dbmod.Pengawas, pid_mhs).hp, "sudah-diubah-admin-lewat-psql")
        finally:
            if old is None:
                os.environ.pop("PENGAWAS_FILE", None)
            else:
                os.environ["PENGAWAS_FILE"] = old
            os.unlink(f.name)
            with SessionLocal() as sdb:
                sdb.query(dbmod.Pengawas).filter(dbmod.Pengawas.id.in_([pid_mhs, pid_dosen])).delete(synchronize_session=False)
                sdb.commit()

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

    def test_only_super_admin_can_use_akun_menu_and_delete_sessions(self):
        """admin_user.type: "admin" (default) atau "super_admin". Hanya super_admin boleh menu Akun &
        menghapus sesi; admin biasa tetap bisa menu lain. Promosi manual lewat DB/tab Akun ke akun MANAPUN
        (bukan cuma config.SUPER_ADMIN_USERNAME) PERSISTEN lewat _sync_admin_types() selama sudah ada
        >=1 super_admin -- itu yg dulu jadi bug (reverted paksa tiap restart server, lihat
        auth.is_superuser & db._sync_admin_types); _other_active_superusers mencegah super_admin
        TERAKHIR yg aktif diturunkan (anti-terkunci)."""
        auth._FAILS.clear()
        for u in ("tes_biasa", "tes_super"):
            r = self.c.post("/api/admin/users", json={"username": u, "password": "rahasia123", "name": u}, headers=ADM)
            self.assertEqual(r.json()["type"], "admin", r.text)          # akun baru selalu "admin"
        old = config.SUPER_ADMIN_USERNAME
        config.SUPER_ADMIN_USERNAME = "tes_super"
        try:
            with SessionLocal() as db:   # promosikan tes_super manual (spt lewat psql/tab Akun)
                db.query(AdminUser).filter(AdminUser.username == "tes_super").update({"type": "super_admin"})
                db.commit()
            dbmod._sync_admin_types()    # sudah ada 1 super_admin -> sync TAK menyentuh apa pun
            with SessionLocal() as db:
                types = {u.username: u.type for u in db.query(AdminUser).filter(AdminUser.username.in_(["tes_biasa", "tes_super"]))}
            self.assertEqual(types, {"tes_biasa": "admin", "tes_super": "super_admin"})

            sid = self.c.post("/api/sessions", json={**VALID, "kelas": "SUPDEL-01"}).json()["id"]
            self.assertEqual(self.c.post("/auth/password", json={"username": "tes_biasa", "password": "rahasia123"}).status_code, 200)
            w = self.c.get("/api/admin/whoami").json()
            self.assertEqual((w["email"], w["superuser"]), ("tes_biasa", False))
            self.assertEqual(self.c.get("/api/admin/summary").status_code, 200)       # admin biasa: menu lain tetap bisa
            self.assertEqual(self.c.get("/api/admin/users").status_code, 403)
            self.assertEqual(self.c.get("/api/admin/access-log").status_code, 403)
            self.assertEqual(self.c.post("/api/admin/users", json={"username": "x_y_z", "password": "rahasia123"}).status_code, 403)
            self.assertEqual(self.c.delete(f"/api/admin/sessions/{sid}").status_code, 403)   # hapus sesi: super_admin saja
            self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/sync-now").status_code, 403)   # "Kirim": super_admin saja
            self.c.post("/auth/logout")
            self.assertEqual(self.c.post("/auth/password", json={"username": "tes_super", "password": "rahasia123"}).status_code, 200)
            self.assertTrue(self.c.get("/api/admin/whoami").json()["superuser"])
            rows = {u["username"]: u["type"] for u in self.c.get("/api/admin/users").json()}
            self.assertEqual((rows["tes_super"], rows["tes_biasa"]), ("super_admin", "admin"))
            self.assertEqual(self.c.get("/api/admin/access-log").status_code, 200)
            self.assertEqual(self.c.delete(f"/api/admin/sessions/{sid}").status_code, 204)
            self.c.post("/auth/logout")
            self.assertTrue(self.c.get("/api/admin/whoami", headers=ADM).json()["superuser"])   # ADMIN_TOKEN = operator

            # Promosikan tes_biasa jadi super_admin lewat tab Akun (PATCH type) -- berlaku seketika & tak
            # ditimpa balik oleh sync berikutnya (beda dari perilaku lama).
            uid_biasa = next(u["id"] for u in self.c.get("/api/admin/users", headers=ADM).json() if u["username"] == "tes_biasa")
            r = self.c.patch(f"/api/admin/users/{uid_biasa}", json={"type": "super_admin"}, headers=ADM)
            self.assertEqual(r.status_code, 200, r.text)
            dbmod._sync_admin_types()
            with SessionLocal() as db:
                self.assertEqual(db.query(AdminUser).filter(AdminUser.username == "tes_biasa").first().type, "super_admin")
            self.assertEqual(self.c.post("/auth/password", json={"username": "tes_biasa", "password": "rahasia123"}).status_code, 200)
            self.assertTrue(self.c.get("/api/admin/whoami").json()["superuser"])   # sekarang super_admin jg, walau bukan SUPER_ADMIN_USERNAME
            self.c.post("/auth/logout")

            # Anti-terkunci: tak boleh menurunkan super_admin TERAKHIR yg masih aktif.
            uid_super = next(u["id"] for u in self.c.get("/api/admin/users", headers=ADM).json() if u["username"] == "tes_super")
            self.assertEqual(self.c.patch(f"/api/admin/users/{uid_super}", json={"type": "admin"}, headers=ADM).status_code, 200)   # tes_biasa msh super_admin, boleh
            self.assertEqual(self.c.patch(f"/api/admin/users/{uid_biasa}", json={"type": "admin"}, headers=ADM).status_code, 409)   # tes_biasa satu2nya yg tersisa -> ditolak
        finally:
            config.SUPER_ADMIN_USERNAME = old
            self.c.post("/auth/logout")

    def test_super_admin_registers_gmail_login_without_restart(self):
        """Email yg boleh masuk admin dikelola di tabel admin_user (username=email, tanpa password) lewat tab
        Akun -- berlaku seketika (auth.is_allowed*), bisa dinonaktifkan/dihapus, ADMIN_EMAILS env di-seed ke sini."""
        email = "tes.gmail@gmail.com"
        self.assertFalse(auth.is_allowed(email))
        r = self.c.post("/api/admin/users/email", json={"email": " Tes.Gmail@Gmail.com ", "name": "Tes"}, headers=ADM)
        self.assertEqual(r.status_code, 200, r.text)
        row = r.json()
        self.assertEqual((row["username"], row["type"], row["sso_only"]), (email, "admin", True))
        self.assertTrue(auth.is_allowed(email))
        self.assertTrue(auth.is_allowed_google({"email": email, "email_verified": True}))
        self.assertFalse(auth.is_allowed_google({"email": email, "email_verified": False}))   # wajib terverifikasi
        self.assertEqual(self.c.post("/api/admin/users/email", json={"email": email}, headers=ADM).status_code, 409)
        self.assertEqual(self.c.post("/api/admin/users/email", json={"email": "bukan-email"}, headers=ADM).status_code, 422)
        self.assertEqual(self.c.patch(f"/api/admin/users/{row['id']}", json={"password": "rahasia123"}, headers=ADM).status_code, 409)
        # akun email tak bisa login lewat password
        auth._FAILS.clear()
        self.assertEqual(self.c.post("/auth/password", json={"username": email, "password": ""}).status_code, 401)
        # nonaktif -> tak boleh masuk lagi
        self.c.patch(f"/api/admin/users/{row['id']}", json={"active": False}, headers=ADM)
        self.assertFalse(auth.is_allowed(email))
        self.c.delete(f"/api/admin/users/{row['id']}", headers=ADM)
        # seed dari ADMIN_EMAILS env
        old = config.ADMIN_EMAILS
        config.ADMIN_EMAILS = ["tes.env@gmail.com"]
        try:
            dbmod._seed_env_admin_emails()
            with SessionLocal() as db:
                u = db.query(AdminUser).filter(AdminUser.username == "tes.env@gmail.com").one()
                self.assertEqual((u.password_hash, u.type), ("", "admin"))
                db.delete(u); db.commit()
        finally:
            config.ADMIN_EMAILS = old
        # admin biasa tak boleh menambah email
        auth._FAILS.clear()
        self.c.post("/api/admin/users", json={"username": "tes_em_biasa", "password": "rahasia123"}, headers=ADM)
        self.assertEqual(self.c.post("/auth/password", json={"username": "tes_em_biasa", "password": "rahasia123"}).status_code, 200)
        self.assertEqual(self.c.post("/api/admin/users/email", json={"email": "x@gmail.com"}).status_code, 403)
        self.c.post("/auth/logout")

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

    def test_admin_validate_all_sheets_validates_every_unvalidated_sheet(self):
        # submitted_session() ikut memvalidasi semua lembar via validate-all pengawas -- reset dulu lewat
        # unvalidate-all-sheets supaya starting point-nya benar2 "belum ada yg tervalidasi".
        sid = self.submitted_session("ADMVALALL-01", n=3)
        self.c.post(f"/api/admin/sessions/{sid}/unvalidate-all-sheets", headers=ADM)
        items = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertTrue(all(not x["validated"] for x in items))
        # satu lembar divalidasi manual dulu -- harus TAK ikut kehitung lagi di "divalidasi" (sudah valid)
        self.c.post(f"/api/admin/sheets/{items[0]['id']}/validate", headers=ADM)

        r = self.c.post(f"/api/admin/sessions/{sid}/validate-all-sheets", headers=ADM)
        self.assertEqual((r.status_code, r.json()["divalidasi"]), (200, 2))
        items2 = self.c.get(f"/api/admin/sessions/{sid}/sheets", headers=ADM).json()["items"]
        self.assertTrue(all(x["validated"] for x in items2))
        # dipanggil lagi saat semua sudah tervalidasi -> tak ada yg baru "divalidasi" (bukan error)
        r2 = self.c.post(f"/api/admin/sessions/{sid}/validate-all-sheets", headers=ADM)
        self.assertEqual(r2.json()["divalidasi"], 0)
        self.assertEqual(self.c.post(f"/api/admin/sessions/{sid}/validate-all-sheets").status_code, 401)
        self.assertEqual(self.c.post("/api/admin/sessions/tidak-ada/validate-all-sheets", headers=ADM).status_code, 404)

    def test_admin_unvalidate_all_sheets_clears_every_sheet_without_touching_session_submitted(self):
        sid = self.submitted_session("ADMUNVAL-01", n=3)
        self.c.post(f"/api/admin/sessions/{sid}/validate", headers=ADM)
        self.c.post(f"/api/admin/sessions/{sid}/sync-now", headers=ADM)   # jadikan submitted beneran ("Kirim")
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
