"""Uji GSheetsClient.upsert_records langsung (bukan lewat FakeSheets spt test_services.py) -- khusus
memastikan baris yg sudah ADA di Sheet diperbarui lewat SATU panggilan ws.batch_update (bukan satu
ws.update per baris), krn itulah yg membuat "Kirim semua tervalidasi"/"Kirim ulang semua" (tombol
super_admin, lihat server/admin.py) aman dipanggil utk ratusan/ribuan baris sekaligus tanpa kena limit
kuota tulis Google Sheets API. Worksheet gspread asli DIPALSUKAN di sini (tak ada panggilan jaringan)."""
import unittest

from server.sheets import GSheetsClient


class FakeWorksheet:
    def __init__(self, header, data_rows, sheet_id=0):
        self._header = list(header)
        self._rows = [list(r) for r in data_rows]   # tak termasuk header
        self.id = sheet_id
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
        for item in data:
            row_num = int(item["range"][1:])   # "A<n>", 1-indexed termasuk header
            self._rows[row_num - 2] = item["values"][0]

    def append_rows(self, rows, value_input_option=None):
        self.append_rows_calls.append(rows)
        self._rows.extend(rows)


class FakeSpreadsheet:
    def __init__(self, ws):
        self._ws = ws
        self.batch_update_calls = []   # panggilan LEVEL-SPREADSHEET (deleteDimension), beda dr ws.batch_update

    def worksheet(self, name):
        return self._ws

    def batch_update(self, body):
        self.batch_update_calls.append(body)
        for req in sorted(body["requests"], key=lambda r: -r["deleteDimension"]["range"]["startIndex"]):
            self._apply_delete(req["deleteDimension"]["range"]["startIndex"])   # 0-indexed termasuk header

    def _apply_delete(self, start_index_incl_header):
        row_i = start_index_incl_header - 1   # ke indeks _rows (header di posisi 0 blm dihitung)
        if 0 <= row_i < len(self._ws._rows):
            del self._ws._rows[row_i]


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


class TestGSheetsClientUpsertValidated(unittest.TestCase):
    def _client_with(self, ws):
        c = GSheetsClient(url="https://example", credentials="{}", worksheet="Sheet1")
        sheet = FakeSpreadsheet(ws)
        c._spreadsheet = lambda: sheet
        return c, sheet

    def test_different_kelas_same_npm_dont_collide(self):
        ws = FakeWorksheet(["NPM", "Kelas", "Nilai"], [])
        client, _ = self._client_with(ws)
        res = client.upsert_validated([{"NPM": "100", "Kelas": "A", "Nilai": "80"},
                                        {"NPM": "100", "Kelas": "B", "Nilai": "90"}])
        self.assertEqual(res, {"updated": 0, "appended": 2, "deleted": 0})
        self.assertEqual(len(ws._rows), 2)   # dua baris TERPISAH, bukan satu saling timpa

    def test_npm_changed_in_class_deletes_stale_row(self):
        """NPM 100 dikoreksi jadi 150 utk kelas A -- baris lama (A_100) harus kehapus, bukan nyangkut jadi
        baris hantu, sementara NPM 200 yg tak berubah tetap aman."""
        ws = FakeWorksheet(["NPM", "Kelas", "Nilai", "Kelas_NPM"],
                            [["100", "A", "80", "A_100"], ["200", "A", "70", "A_200"]])
        client, _ = self._client_with(ws)
        res = client.upsert_validated([{"NPM": "150", "Kelas": "A", "Nilai": "80"},
                                        {"NPM": "200", "Kelas": "A", "Nilai": "70"}])
        self.assertEqual(res, {"updated": 1, "appended": 1, "deleted": 1})
        npms = sorted(r[0] for r in ws._rows)
        self.assertEqual(npms, ["150", "200"])   # 100 hilang, 150 muncul, 200 tetap

    def test_other_kelas_not_synced_this_batch_is_untouched(self):
        """Sinkron kelas A tak boleh menyentuh baris kelas B sama sekali, walau B kebetulan tak ikut
        disertakan di `records` kali ini."""
        ws = FakeWorksheet(["NPM", "Kelas", "Nilai", "Kelas_NPM"],
                            [["100", "A", "80", "A_100"], ["999", "B", "50", "B_999"]])
        client, _ = self._client_with(ws)
        res = client.upsert_validated([{"NPM": "150", "Kelas": "A", "Nilai": "80"}])
        self.assertEqual(res, {"updated": 0, "appended": 1, "deleted": 1})
        kelas_present = {r[1] for r in ws._rows}
        self.assertIn("B", kelas_present)   # baris kelas B masih ada, tak ikut terhapus
        self.assertEqual(sorted(r[0] for r in ws._rows if r[1] == "B"), ["999"])


if __name__ == "__main__":
    unittest.main()
