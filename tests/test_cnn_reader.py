"""Pembaca cadangan NPM berbasis CNN (core.cnn_reader) — bobot dilatih di luar repo dari lembar ujian asli
(lihat core/cnn_reader.py). Tes ini memeriksa properti KEAMANAN-nya (tak pernah menebak paksa di kertas
kosong, gerbang keyakinan bekerja, bobot hilang tak membuat pemanggil gagal) dengan bobot ASLI yang
dibundel di repo — bukan menguji akurasi baca (itu diukur terpisah lewat validasi silang, di luar repo).
"""
import unittest

import numpy as np

import core.cnn_reader as cr
from scanner.service import _cnn_rescue_npm, load_default_template


class TestCnnReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.field = load_default_template()["fields"]["NPM"]

    def test_weights_bundled_and_load(self):
        self.assertTrue(cr.available(), "bobot core/models/nim_cnn.npz tidak ditemukan/tidak termuat")

    def test_blank_paper_never_hallucinates_a_digit(self):
        blank = np.full((2400, 1700), 255, np.uint8)
        out = cr.read_missing_npm_digits(blank, self.field, list(range(10)))
        self.assertEqual(out, {})

    def test_missing_weights_degrades_to_no_op(self):
        cr._weights.cache_clear()
        old = cr._WEIGHTS_PATH
        cr._WEIGHTS_PATH = old + ".tidak-ada"
        try:
            out = cr.read_missing_npm_digits(np.full((200, 200), 255, np.uint8), self.field, [0, 1])
            self.assertEqual(out, {})
            self.assertFalse(cr.available())
        finally:
            cr._WEIGHTS_PATH = old
            cr._weights.cache_clear()

    def test_empty_column_list_short_circuits(self):
        self.assertEqual(cr.read_missing_npm_digits(np.zeros((10, 10), np.uint8), self.field, []), {})


class TestCnnRescueNpm(unittest.TestCase):
    """_cnn_rescue_npm (scanner/service.py): hanya mengisi kolom yang belum terbaca; tak pernah dipanggil
    sama sekali (apalagi mengubah apa pun) bila NPM sudah lengkap."""

    @classmethod
    def setUpClass(cls):
        cls.fields = {"NPM": load_default_template()["fields"]["NPM"]}

    def test_fully_resolved_npm_short_circuits_without_touching_model(self):
        called = []
        orig = cr.read_missing_npm_digits
        cr.read_missing_npm_digits = lambda *a, **k: called.append(1) or {}
        try:
            npm, filled = _cnn_rescue_npm(np.zeros((10, 10), np.uint8), self.fields, {"NPM": "1032600255"}, "tes")
        finally:
            cr.read_missing_npm_digits = orig
        self.assertEqual((npm, filled, called), ("1032600255", {}, []))

    def test_blank_paper_leaves_npm_unresolved(self):
        blank = np.full((2400, 1700), 255, np.uint8)
        npm, filled = _cnn_rescue_npm(blank, self.fields, {"NPM": ""}, "tes")
        self.assertEqual((npm, filled), ("", {}))

    def test_missing_field_is_a_no_op(self):
        npm, filled = _cnn_rescue_npm(np.zeros((10, 10), np.uint8), {}, {"NPM": " "}, "tes")
        self.assertEqual((npm, filled), (" ", {}))


if __name__ == "__main__":
    unittest.main()
