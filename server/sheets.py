"""Klien Google Sheet (gspread) — rekap hasil dan kunci jawaban. Tidak bergantung pada Streamlit."""
import json
import os
import threading

from server import config


def load_toml_credentials(path):
    """Baca berkas Streamlit secrets.toml ([connections.gsheets]) -> (kredensial service account, URL spreadsheet).
    Memakai berkas yang sama dengan versi Streamlit sehingga kunci privat tidak perlu disalin."""
    import tomllib
    with open(path, "rb") as f:
        cfg = tomllib.load(f)["connections"]["gsheets"]
    url = cfg.get("spreadsheet", "")
    keys = ("type", "project_id", "private_key_id", "private_key", "client_email", "client_id",
            "auth_uri", "token_uri", "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain")
    return {k: cfg[k] for k in keys if k in cfg}, url


class SheetsUnavailable(RuntimeError):
    """Google Sheet belum dikonfigurasi (GSHEET_URL / GSHEET_CREDENTIALS)."""


class GSheetsClient:
    def __init__(self, url, credentials, worksheet):
        self.url, self.credentials, self.worksheet = url, credentials, worksheet
        self._gc = None
        self._lock = threading.Lock()   # satu operasi tulis pada satu waktu (header + append harus berurutan)

    def _client(self):
        if self._gc is None:
            import gspread
            c = self.credentials
            if c.lstrip().startswith("{"):
                self._gc = gspread.service_account_from_dict(json.loads(c))
            elif c.lower().endswith(".toml"):
                self._gc = gspread.service_account_from_dict(load_toml_credentials(c)[0])
            else:
                self._gc = gspread.service_account(filename=c)
        return self._gc

    def _spreadsheet(self):
        return self._client().open_by_url(self.url)

    def _worksheet_and_columns(self, sh, records, ws_name=None):
        """Ambil/buat worksheet rekap & pastikan header mencakup semua kolom `records` -- dipakai
        append_records & upsert_records supaya keduanya konsisten nambah kolom baru. `ws_name` default ke
        tab rekap utama (self.worksheet); upsert_validated mengoper tab lain (lihat GSHEET_VALIDATED_WORKSHEET)."""
        ws_name = ws_name or self.worksheet
        try:
            ws = sh.worksheet(ws_name)
        except Exception:
            ws = sh.add_worksheet(title=ws_name, rows=1000, cols=40)
        header = ws.row_values(1)
        cols = list(header)
        for r in records:
            for k in r:
                if k not in cols:
                    cols.append(k)
        if cols != header:
            ws.update(values=[cols], range_name="A1")
        return ws, cols

    def append_records(self, records):
        """Tambahkan baris hasil ke tab rekap dalam SATU permintaan batch. Kolom baru ditambahkan ke header."""
        if not records:
            return 0
        with self._lock:
            sh = self._spreadsheet()
            ws, cols = self._worksheet_and_columns(sh, records)
            rows = [[("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in records]
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            return len(rows)

    def upsert_records(self, records, key_col="NPM", ws_name=None):
        """Spt append_records, tapi baris yg `key_col`-nya SAMA dgn yg sudah ada di Sheet diPERBARUI di
        tempat (bukan ditambah lagi) -- dipakai tombol "Kirim" manual per sesi di admin, yg boleh dipakai
        BERULANG KALI (mis. stlh admin mengoreksi data lewat bulk-edit/edit lembar) tanpa menggandakan
        baris. append_records TETAP dipakai apa adanya utk sinkron otomatis berkala (services.
        sync_pending_once) krn itu cuma pernah memproses sesi yg BELUM PERNAH tersinkron sama sekali --
        tak butuh & tak perlu kena biaya pencarian baris ini. `ws_name` opsional: tab tujuan selain rekap
        utama (dipakai upsert_validated utk nulis ke tab 'validated' yg key-nya Kelas+NPM)."""
        if not records:
            return {"updated": 0, "appended": 0}
        with self._lock:
            sh = self._spreadsheet()
            ws, cols = self._worksheet_and_columns(sh, records, ws_name)
            key_idx = cols.index(key_col) if key_col in cols else None
            existing_row = {}   # nilai key_col -> nomor baris (1-indexed, termasuk header)
            if key_idx is not None:
                for i, row in enumerate(ws.get_all_values()[1:], start=2):
                    if len(row) > key_idx and row[key_idx]:
                        existing_row.setdefault(row[key_idx], i)
            updated = 0
            to_append = []
            to_update = []   # {"range": "A<row>", "values": [[...]]} per baris yg sudah ada -- lihat di bawah
            for r in records:
                key_val = str(r.get(key_col, "") or "").strip()
                row_vals = [("" if r.get(c) is None else str(r.get(c))) for c in cols]
                row_num = existing_row.get(key_val) if key_val else None
                if row_num:
                    to_update.append({"range": f"A{row_num}", "values": [row_vals]})
                    updated += 1
                else:
                    to_append.append(row_vals)
            if to_update:
                # SATU permintaan utk SEMUA baris yg diperbarui (ws.batch_update), bukan satu ws.update per
                # baris -- krn "Kirim semua tervalidasi"/"Kirim ulang semua" (admin.py, tombol super_admin)
                # bisa mencakup ratusan/ribuan baris sekaligus; ws.update per baris di situ akan kena limit
                # kuota tulis Google Sheets API (bbrp lusin per menit) & bisa makan waktu berjam-jam/gagal
                # di tengah jalan. batch_update menyelesaikannya dlm 1 permintaan API apa pun jumlah barisnya.
                ws.batch_update(to_update, value_input_option="USER_ENTERED")
            if to_append:
                ws.append_rows(to_append, value_input_option="USER_ENTERED")
            return {"updated": updated, "appended": len(to_append)}

    def upsert_validated(self, records, key_cols=("Kelas", "NPM"), ws_name=None):
        """Spt upsert_records, tapi key cocoknya KOMBINASI `key_cols` (default Kelas+NPM), bukan NPM saja --
        ditulis ke tab TERPISAH (default GSHEET_VALIDATED_WORKSHEET = 'validated'), bukan menimpa tab rekap
        utama. Alasannya: upsert_records(key_col='NPM') di tab rekap utama mencocokkan baris HANYA dari
        NPM -- kalau ada salah input (pengawas/mahasiswa salah ketik NPM shg kebetulan sama dgn NPM
        mahasiswa lain di KELAS BERBEDA), baris kelas lain itu bisa KETIMPA tanpa sadar. Key komposit
        Kelas+NPM di tab 'validated' ini membuat tabrakan itu mustahil: beda kelas = beda baris, apa pun
        NPM-nya. Kolom komposit itu sendiri ikut ditulis apa adanya (field "Kelas_NPM") spy gampang
        ditelusuri manual di Sheet-nya.

        SELAIN upsert, utk tiap KELAS yg muncul di `records`, baris LAMA di tab ini yg kelasnya sama tapi
        NPM-nya TAK ADA lagi di `records` kali ini ikut DIHAPUS (bukan cuma dibiarkan nyangkut) -- krn NPM
        bisa berubah (admin koreksi NIM/NPM yg salah input pengawas/mahasiswa lewat edit lembar), jadi baris
        lama dgn NPM yg sudah tak berlaku utk kelas itu harus ikut hilang, bukan nambah jadi baris "hantu"
        yg tetap keitung di rekap. Cakupan hapusnya SENGAJA dibatasi per kelas yg benar2 ada di `records`
        (bukan seisi tab) -- jadi kelas lain yg kebetulan tak ikut disinkron kali ini tak tersentuh sama
        sekali. `records` yg dioper ke sini HARUS daftar LENGKAP lembar suatu sesi/kelas (bukan sebagian),
        krn yg tak disertakan dianggap "sudah tak berlaku lagi" -- semua pemanggil di services.py sudah
        begitu (s.sheets = seluruh lembar sesi itu)."""
        ws_name = ws_name or config.GSHEET_VALIDATED_WORKSHEET
        kelas_col = key_cols[0]
        key_col = "_".join(key_cols)
        if not records:
            return {"updated": 0, "appended": 0, "deleted": 0}
        tagged = []
        for r in records:
            r2 = dict(r)
            r2[key_col] = "_".join(str(r.get(c, "") or "").strip() for c in key_cols)
            tagged.append(r2)
        with self._lock:
            sh = self._spreadsheet()
            ws, cols = self._worksheet_and_columns(sh, tagged, ws_name)
            kelas_idx = cols.index(kelas_col) if kelas_col in cols else None
            key_idx = cols.index(key_col)
            existing_row = {}          # key komposit -> nomor baris
            existing_by_kelas = {}     # nilai Kelas -> {key komposit -> nomor baris}, dipakai cari baris usang
            for i, row in enumerate(ws.get_all_values()[1:], start=2):
                key_val = row[key_idx] if len(row) > key_idx else ""
                kelas_val = row[kelas_idx] if kelas_idx is not None and len(row) > kelas_idx else ""
                if key_val:
                    existing_row.setdefault(key_val, i)
                if kelas_val and key_val:
                    existing_by_kelas.setdefault(kelas_val, {})[key_val] = i

            keys_now_by_kelas = {}   # Kelas -> set key komposit yg ADA di `records` kali ini
            for r in tagged:
                kv = str(r.get(kelas_col, "") or "").strip()
                keys_now_by_kelas.setdefault(kv, set()).add(r[key_col])

            updated = 0
            to_update, to_append = [], []
            for r in tagged:
                key_val = r[key_col]
                row_vals = [("" if r.get(c) is None else str(r.get(c))) for c in cols]
                row_num = existing_row.get(key_val) if key_val else None
                if row_num:
                    to_update.append({"range": f"A{row_num}", "values": [row_vals]})
                    updated += 1
                else:
                    to_append.append(row_vals)

            stale_rows = sorted(
                (row_num for kelas_val, wanted in keys_now_by_kelas.items()
                 for k, row_num in existing_by_kelas.get(kelas_val, {}).items() if k not in wanted),
                reverse=True)   # urutan MENURUN wajib -- hapus dari bawah dulu spy nomor baris yg BELUM
                                 # dihapus (lbh kecil) tetap valid stlh tiap panggilan delete

            if to_update:
                ws.batch_update(to_update, value_input_option="USER_ENTERED")
            if stale_rows:
                requests = [{"deleteDimension": {"range": {"sheetId": ws.id, "dimension": "ROWS",
                             "startIndex": rn - 1, "endIndex": rn}}} for rn in stale_rows]
                sh.batch_update({"requests": requests})
            if to_append:
                ws.append_rows(to_append, value_input_option="USER_ENTERED")
            return {"updated": updated, "appended": len(to_append), "deleted": len(stale_rows)}

    def find_duplicates(self, key_col="NPM"):
        """Baca tab rekap APA ADANYA & kelompokkan nomor baris per nilai `key_col` -- dipakai utk diagnosa
        'kenapa jumlah baris di Sheet > jumlah terkirim di dashboard' (lihat admin.admin_sheet_duplicates):
        auto-sync berkala (append_records) cuma dedup per SESI, bukan lintas sesi, jadi NPM yg sama muncul
        di >1 ScanSession (mis. diunggah ulang sbg sesi/kelas baru) bisa numpuk jadi >1 baris di Sheet yg
        tak pernah ketahuan krn upsert_records cuma dipakai di jalur "Kirim" manual. Hanya baca, tak ubah apa pun."""
        sh = self._spreadsheet()
        try:
            ws = sh.worksheet(self.worksheet)
        except Exception:
            return {"total_rows": 0, "duplicates": []}
        values = ws.get_all_values()
        if not values:
            return {"total_rows": 0, "duplicates": []}
        header = values[0]
        if key_col not in header:
            return {"total_rows": len(values) - 1, "duplicates": [], "error": f"Kolom '{key_col}' tak ditemukan di header"}
        key_idx = header.index(key_col)
        rows_by_key = {}
        for i, row in enumerate(values[1:], start=2):
            key_val = (row[key_idx] if len(row) > key_idx else "").strip()
            if key_val:
                rows_by_key.setdefault(key_val, []).append(i)
        dupes = [{"npm": k, "count": len(v), "rows": v} for k, v in rows_by_key.items() if len(v) > 1]
        dupes.sort(key=lambda d: -d["count"])
        return {"total_rows": len(values) - 1, "unique_keys": len(rows_by_key),
                "duplicates": dupes, "duplicate_extra_rows": sum(d["count"] - 1 for d in dupes)}

    def fetch_kunci(self):
        """Ambil semua tab kunci jawaban: {nama_tab: {nomor: jawaban}}."""
        from core.evaluator import parse_kunci_jawaban_raw_rows
        skip = {"sheet1", "rekap", "database", "hasil", "hasilpenilaian", "mahasiswa", "summary", self.worksheet.lower()}
        out = {}
        for ws in self._spreadsheet().worksheets():
            title = ws.title.strip()
            if title.lower().replace(" ", "").replace("_", "").replace("-", "") in skip:
                continue
            values = ws.get_all_values()
            q = parse_kunci_jawaban_raw_rows(values) if values else {}
            if len(q) >= 3:
                out[title] = q
        return out


_client = None
_override = None


def set_client(c):
    """Untuk pengujian: ganti klien dengan tiruan."""
    global _override
    _override = c


def get_client():
    """Klien terkonfigurasi, atau None bila belum diatur."""
    global _client
    if _override is not None:
        return _override
    if not config.GSHEET_CREDENTIALS:
        return None
    url = config.GSHEET_URL
    if not url and config.GSHEET_CREDENTIALS.lower().endswith(".toml"):
        url = load_toml_credentials(config.GSHEET_CREDENTIALS)[1]   # URL dari secrets.toml bila GSHEET_URL tidak diisi
    if not url:
        return None
    if _client is None:
        _client = GSheetsClient(url, config.GSHEET_CREDENTIALS, config.GSHEET_WORKSHEET)
    return _client
