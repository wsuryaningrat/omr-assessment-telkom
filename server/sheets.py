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

    def append_records(self, records):
        """Tambahkan baris hasil ke tab rekap dalam SATU permintaan batch. Kolom baru ditambahkan ke header."""
        if not records:
            return 0
        with self._lock:
            sh = self._spreadsheet()
            try:
                ws = sh.worksheet(self.worksheet)
            except Exception:
                ws = sh.add_worksheet(title=self.worksheet, rows=1000, cols=40)
            header = ws.row_values(1)
            cols = list(header)
            for r in records:
                for k in r:
                    if k not in cols:
                        cols.append(k)
            if cols != header:
                ws.update(values=[cols], range_name="A1")
            rows = [[("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in records]
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            return len(rows)

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
