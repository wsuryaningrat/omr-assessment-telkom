"""core.npm_locator: pencari posisi blok NPM lewat CNN sendiri (upaya terakhir saat potongan halaman
meleset jauh). Tes ini memeriksa properti KEAMANAN (tak pernah menebak paksa di kertas kosong, degradasi
aman bila bobot tak ada) dan integrasi scanner.service._npm_locator_rescue -- BUKAN akurasi baca (lambat,
diukur terpisah di luar repo pada foto ujian asli; lihat catatan di core/npm_locator.py)."""
import time
import unittest

import numpy as np

import core.cnn_reader as cr
import core.npm_locator as loc
from scanner.service import (
    _choice_field_complete,
    _choice_locator_rescue,
    _digit_field_complete,
    _digit_locator_rescue,
    _npm_complete,
    _npm_locator_rescue,
    load_default_template,
)


class TestNpmLocator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.field = load_default_template()["fields"]["NPM"]

    def test_missing_weights_returns_empty_without_search(self):
        cr._weights.cache_clear()
        old = cr._WEIGHTS_PATH
        cr._WEIGHTS_PATH = old + ".tidak-ada"
        try:
            out, pose, score, timed_out = loc.read(np.zeros((100, 100), np.uint8), self.field)
            self.assertEqual((out, score, timed_out), ({}, 0.0, False))
        finally:
            cr._WEIGHTS_PATH = old
            cr._weights.cache_clear()

    def test_blank_paper_never_hallucinates_a_digit(self):
        # kertas kosong penuh (bukan sekadar area sempit) -- properti keselamatan paling penting fungsi ini.
        # Jangkauan pencarian dipersempit hanya utk tes ini (jalur asli lambat -- ~1-3 menit di kertas
        # kosong, krn tak pernah cukup yakin utk berhenti awal); logika yg diuji sama persis.
        # (catatan: `scales` adalah default arg `locate()`, ditangkap saat definisi -- menimpa loc._SCALES
        # di sini tak berpengaruh; yg dipersempit adalah jangkauan geser, dibaca dinamis di badan fungsi.)
        old = (loc._FAST_RANGE, loc._FAST_STEP, loc._WIDE_RANGE, loc._WIDE_STEP)
        loc._FAST_RANGE, loc._FAST_STEP, loc._WIDE_RANGE, loc._WIDE_STEP = 12, 12, 12, 12
        try:
            blank = np.full((2400, 1700), 255, np.uint8)
            out, pose, score, timed_out = loc.read(blank, self.field)
        finally:
            loc._FAST_RANGE, loc._FAST_STEP, loc._WIDE_RANGE, loc._WIDE_STEP = old
        self.assertEqual(out, {})
        self.assertEqual(score, 0.0)
        self.assertFalse(timed_out)   # tahap cepat selesai jauh di bawah budget_s, jadi bukan timeout

    def test_budget_forces_early_return_and_flags_timed_out(self):
        # Jangkauan dipersempit spt tes di atas (jalur asli lambat), tapi budget_s dibuat SANGAT kecil supaya
        # locate() dipaksa berhenti sebelum tahap halus -- properti yg diuji: tak pernah menunggu tanpa batas,
        # dan status timed_out=True mengalir keluar (dipakai pemanggil utk logging, bukan disembunyikan).
        old = (loc._FAST_RANGE, loc._FAST_STEP, loc._WIDE_RANGE, loc._WIDE_STEP, loc._GOOD_ENOUGH_SCORE)
        loc._FAST_RANGE, loc._FAST_STEP, loc._WIDE_RANGE, loc._WIDE_STEP = 12, 12, 12, 12
        loc._GOOD_ENOUGH_SCORE = 999.0   # tahap cepat tak akan pernah "cukup yakin" -> selalu masuk tahap lebar
        try:
            blank = np.full((2400, 1700), 255, np.uint8)
            t0 = time.time()
            (dx, dy, s), score, probs, elapsed, timed_out = loc.locate(blank, self.field, budget_s=0.001)
            wall = time.time() - t0
        finally:
            loc._FAST_RANGE, loc._FAST_STEP, loc._WIDE_RANGE, loc._WIDE_STEP, loc._GOOD_ENOUGH_SCORE = old
        self.assertTrue(timed_out)
        # tetap mengembalikan hasil TERBAIK SEJAUH ITU (tahap cepat), bukan error/None.
        self.assertEqual(probs.shape, (len(self.field["items"]) * len(self.field["items"][0]["bubbles"]),))
        # tak menunggu berlama2 (jauh di bawah anggaran waktu tahap lebar/halus penuh).
        self.assertLess(wall, 30.0)


class TestNpmLocatorRescueIntegration(unittest.TestCase):
    """_npm_locator_rescue (scanner/service.py): hanya dipanggil/berbunyi bila NPM MASIH tak lengkap setelah
    jalur yg lebih murah; tak pernah dipanggil (apalagi mengubah apa pun) bila NPM sudah lengkap."""

    @classmethod
    def setUpClass(cls):
        cls.fields = {"NPM": load_default_template()["fields"]["NPM"]}

    def test_complete_npm_short_circuits_without_touching_model(self):
        called = []
        orig = loc.read
        loc.read = lambda *a, **k: (called.append(1), {}, (0, 0, 1), 0.0)[1:]
        try:
            npm = _npm_locator_rescue(np.zeros((10, 10), np.uint8), self.fields, {"NPM": "1032600255"}, "tes")
        finally:
            loc.read = orig
        self.assertEqual((npm, called), ("1032600255", []))

    def test_missing_field_is_a_no_op(self):
        npm = _npm_locator_rescue(np.zeros((10, 10), np.uint8), {}, {"NPM": " "}, "tes")
        self.assertEqual(npm, " ")

    def test_exception_in_search_does_not_propagate(self):
        orig = loc.read
        loc.read = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dijaga"))
        try:
            npm = _npm_locator_rescue(np.zeros((10, 10), np.uint8), self.fields, {"NPM": ""}, "tes")
        finally:
            loc.read = orig
        self.assertEqual(npm, "")   # upaya terakhir gagal -> nilai lama dipertahankan, tak menggagalkan scan

    def test_npm_complete_helper(self):
        self.assertTrue(_npm_complete("1032600255", self.fields))
        self.assertFalse(_npm_complete("103260025", self.fields))    # 9 digit
        self.assertFalse(_npm_complete("103260025?", self.fields))   # ada '?'
        self.assertFalse(_npm_complete("103260025 ", self.fields))   # ada spasi


class TestDigitLocatorRescueGeneric(unittest.TestCase):
    """_digit_locator_rescue (scanner/service.py) generalisasi _npm_locator_rescue ke field digit apa pun --
    di sini diuji dgn KODE SOAL (struktur identik NPM: 3 kolom x 10 opsi)."""

    @classmethod
    def setUpClass(cls):
        cls.fields = {"KODE SOAL": load_default_template()["fields"]["KODE SOAL"]}

    def test_complete_kode_soal_short_circuits_without_touching_model(self):
        called = []
        orig = loc.read
        loc.read = lambda *a, **k: called.append(1) or ({}, (0, 0, 1), 0.0, False)
        try:
            val, timed_out = _digit_locator_rescue(
                np.zeros((10, 10), np.uint8), self.fields, {"KODE SOAL": "025"}, "tes", "KODE SOAL")
        finally:
            loc.read = orig
        self.assertEqual((val, timed_out, called), ("025", False, []))

    def test_missing_field_is_a_no_op(self):
        val, timed_out = _digit_locator_rescue(np.zeros((10, 10), np.uint8), {}, {"KODE SOAL": " "}, "tes", "KODE SOAL")
        self.assertEqual((val, timed_out), (" ", False))

    def test_exception_in_search_does_not_propagate(self):
        orig = loc.read
        loc.read = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dijaga"))
        try:
            val, timed_out = _digit_locator_rescue(
                np.zeros((10, 10), np.uint8), self.fields, {"KODE SOAL": ""}, "tes", "KODE SOAL")
        finally:
            loc.read = orig
        self.assertEqual((val, timed_out), ("", False))

    def test_timeout_status_propagates_from_read(self):
        orig = loc.read
        loc.read = lambda *a, **k: ({1: "5"}, (0, 0, 1), 0.4, True)
        try:
            val, timed_out = _digit_locator_rescue(
                np.zeros((10, 10), np.uint8), self.fields, {"KODE SOAL": ""}, "tes", "KODE SOAL")
        finally:
            loc.read = orig
        self.assertTrue(timed_out)   # status timeout dari core.npm_locator.read() harus terus mengalir keluar

    def test_digit_field_complete_helper(self):
        field = self.fields["KODE SOAL"]
        self.assertTrue(_digit_field_complete("025", field))
        self.assertFalse(_digit_field_complete("02", field))   # 2 digit (perlu 3)
        self.assertFalse(_digit_field_complete("02?", field))


class TestChoiceLocatorRescue(unittest.TestCase):
    """_choice_locator_rescue (scanner/service.py): sama spt _digit_locator_rescue tapi utk field pilihan-
    tunggal 1-kolom (FAKULTAS) -- 'terisi' berarti tepat satu opsi, bukan string per-digit."""

    @classmethod
    def setUpClass(cls):
        cls.fields = {"FAKULTAS": load_default_template()["fields"]["FAKULTAS"]}

    def test_complete_fakultas_short_circuits_without_touching_model(self):
        called = []
        orig = loc.read
        loc.read = lambda *a, **k: called.append(1) or ({}, (0, 0, 1), 0.0, False)
        try:
            val, timed_out = _choice_locator_rescue(
                np.zeros((10, 10), np.uint8), self.fields, {"FAKULTAS": "FIF"}, "tes", "FAKULTAS")
        finally:
            loc.read = orig
        self.assertEqual((val, timed_out, called), ("FIF", False, []))

    def test_blank_fakultas_uses_locator_result_when_confident(self):
        orig = loc.read
        loc.read = lambda *a, **k: ({0: "FTE"}, (0, 0, 1), 0.6, False)
        try:
            val, timed_out = _choice_locator_rescue(
                np.zeros((10, 10), np.uint8), self.fields, {"FAKULTAS": "BLANK"}, "tes", "FAKULTAS")
        finally:
            loc.read = orig
        self.assertEqual((val, timed_out), ("FTE", False))

    def test_unresolved_locator_result_leaves_value_untouched(self):
        orig = loc.read
        loc.read = lambda *a, **k: ({}, (0, 0, 1), 0.1, False)   # gerbang tak lolos -> tak menebak paksa
        try:
            val, timed_out = _choice_locator_rescue(
                np.zeros((10, 10), np.uint8), self.fields, {"FAKULTAS": "BLANK"}, "tes", "FAKULTAS")
        finally:
            loc.read = orig
        self.assertEqual((val, timed_out), ("BLANK", False))

    def test_choice_field_complete_helper(self):
        field = self.fields["FAKULTAS"]
        self.assertTrue(_choice_field_complete("FIF", field))
        self.assertFalse(_choice_field_complete("BLANK", field))
        self.assertFalse(_choice_field_complete("?", field))
        self.assertFalse(_choice_field_complete("MULTIPLE", field))
        self.assertFalse(_choice_field_complete("", field))


if __name__ == "__main__":
    unittest.main()
