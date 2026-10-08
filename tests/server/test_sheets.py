"""Uji GSheetsClient.upsert_records langsung (bukan lewat FakeSheets spt test_services.py) -- khusus
memastikan baris yg sudah ADA di Sheet diperbarui lewat SATU panggilan ws.batch_update (bukan satu
ws.update per baris), krn itulah yg membuat "Kirim semua tervalidasi"/"Kirim ulang semua" (tombol
super_admin, lihat server/admin.py) aman dipanggil utk ratusan/ribuan baris sekaligus tanpa kena limit
kuota tulis Google Sheets API. Worksheet gspread asli DIPALSUKAN di sini (tak ada panggilan jaringan)."""
import unittest

from server.sheets import GSheetsClient


class FakeWorksheet:
    def __init__(self, header, data_rows):
        self._header = list(header)
        self._rows = [list(r) for r in data_rows]   # tak termasuk header
        self.update_calls = []
        self.batch_update_calls = []
        self.append_rows_calls = []

    def row_values(self, n):
        assert n == 1
        return self._header

    def get_all_values(self):
        return [self._header] + self._rows

    def update(self, values, range_name):   # noqa: A003 -- nama method gspread asli
        self.update_calls.append((range_name, values))

    def batch_update(self, data, value_input_option=None):
        self.batch_update_calls.append(data)

    def append_rows(self, rows, value_input_option=None):
        self.append_rows_calls.append(rows)


class FakeSpreadsheet:
    def __init__(self, ws):
        self._ws = ws

    def worksheet(self, name):
        return self._ws


class TestGSheetsClientUpsert(unittest.TestCase):
    def _client_with(self, ws):
        c = GSheetsClient(url="https://example", credentials="{}", worksheet="Sheet1")
        c._spreadsheet = lambda: FakeSpreadsheet(ws)   # lewati auth/network sungguhan
        return c

    def test_updates_existing_rows_via_single_batch_update_call(self):
        """3 baris yg NPM-nya SUDAH ada di Sheet -> HARUS jadi SATU panggilan batch_update, bukan 3x
        ws.update (itu pola lama yg diganti -- lihat docstring modul)."""
        ws = FakeWorksheet(["NPM", "Nilai"], [["111", "60"], ["222", "70"], ["333", "80"]])
        client = self._client_with(ws)
        res = client.upsert_records(
            [{"NPM": "111", "Nilai": "90"}, {"NPM": "222", "Nilai": "95"}, {"NPM": "333", "Nilai": "100"}],
            key_col="NPM")
        self.assertEqual(res, {"updated": 3, "appended": 0})
        self.assertEqual(ws.update_calls, [])   # TAK ADA ws.update per-baris lagi
        self.assertEqual(len(ws.batch_update_calls), 1)   # SATU permintaan utk ketiganya
        batch = ws.batch_update_calls[0]
        self.assertEqual({b["range"] for b in batch}, {"A2", "A3", "A4"})
        self.assertEqual(ws.append_rows_calls, [])

    def test_new_npm_goes_to_append_rows_not_batch_update(self):
        ws = FakeWorksheet(["NPM", "Nilai"], [["111", "60"]])
        client = self._client_with(ws)
        res = client.upsert_records([{"NPM": "111", "Nilai": "90"}, {"NPM": "999", "Nilai": "77"}], key_col="NPM")
        self.assertEqual(res, {"updated": 1, "appended": 1})
        self.assertEqual(len(ws.batch_update_calls), 1)   # baris 111 (sudah ada)
        self.assertEqual(len(ws.append_rows_calls), 1)    # baris 999 (baru)
        self.assertEqual(ws.append_rows_calls[0], [["999", "77"]])

    def test_no_existing_rows_match_skips_batch_update_entirely(self):
        ws = FakeWorksheet(["NPM", "Nilai"], [])
        client = self._client_with(ws)
        res = client.upsert_records([{"NPM": "1", "Nilai": "1"}, {"NPM": "2", "Nilai": "2"}], key_col="NPM")
        self.assertEqual(res, {"updated": 0, "appended": 2})
        self.assertEqual(ws.batch_update_calls, [])
        self.assertEqual(len(ws.append_rows_calls), 1)


if __name__ == "__main__":
    unittest.main()
