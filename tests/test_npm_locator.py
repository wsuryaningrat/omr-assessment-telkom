"""core.npm_locator: pencari posisi blok NPM lewat CNN sendiri (upaya terakhir saat potongan halaman
meleset jauh). Tes ini memeriksa properti KEAMANAN (tak pernah menebak paksa di kertas kosong, degradasi
aman bila bobot tak ada) dan integrasi scanner.service._npm_locator_rescue -- BUKAN akurasi baca (lambat,
diukur terpisah di luar repo pada foto ujian asli; lihat catatan di core/npm_locator.py)."""
import unittest

import numpy as np

import core.cnn_reader as cr
import core.npm_locator as loc
from scanner.service import _npm_complete, _npm_locator_rescue, load_default_template


class TestNpmLocator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.field = load_default_template()["fields"]["NPM"]

    def test_missing_weights_returns_empty_without_search(self):
        cr._weights.cache_clear()
        old = cr._WEIGHTS_PATH
        cr._WEIGHTS_PATH = old + ".tidak-ada"
        try:
            out, pose, score = loc.read(np.zeros((100, 100), np.uint8), self.field)
            self.assertEqual((out, score), ({}, 0.0))
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
            out, pose, score = loc.read(blank, self.field)
        finally:
            loc._FAST_RANGE, loc._FAST_STEP, loc._WIDE_RANGE, loc._WIDE_STEP = old
        self.assertEqual(out, {})
        self.assertEqual(score, 0.0)


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


if __name__ == "__main__":
    unittest.main()
