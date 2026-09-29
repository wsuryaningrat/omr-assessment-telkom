"""Pembaca cadangan NPM berbasis CNN (core.cnn_reader) — bobot dilatih di luar repo dari lembar ujian asli
(lihat core/cnn_reader.py). Tes ini memeriksa properti KEAMANAN-nya (tak pernah menebak paksa di kertas
kosong, gerbang keyakinan bekerja, bobot hilang tak membuat pemanggil gagal) dengan bobot ASLI yang
dibundel di repo — bukan menguji akurasi baca (itu diukur terpisah lewat validasi silang, di luar repo).
"""
import unittest

import numpy as np

import core.cnn_reader as cr
import scanner.service as svc
from scanner.service import _cnn_rescue_choice, _cnn_rescue_digits, _cnn_rescue_npm, load_default_template

# scanner/service.py pakai `from core.cnn_reader import read_missing_npm_digits` (impor nama langsung) --
# menimpa cr.read_missing_npm_digits TIDAK memengaruhi ikatan itu. Tes yg benar2 memanggil fungsi ini (bukan
# cuma jalur short-circuit) harus menimpa svc.read_missing_npm_digits, bukan cr.read_missing_npm_digits.


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
        orig = svc.read_missing_npm_digits
        svc.read_missing_npm_digits = lambda *a, **k: called.append(1) or {}
        try:
            npm, filled = _cnn_rescue_npm(np.zeros((10, 10), np.uint8), self.fields, {"NPM": "1032600255"}, "tes")
        finally:
            svc.read_missing_npm_digits = orig
        self.assertEqual((npm, filled, called), ("1032600255", {}, []))

    def test_blank_paper_leaves_npm_unresolved(self):
        blank = np.full((2400, 1700), 255, np.uint8)
        npm, filled = _cnn_rescue_npm(blank, self.fields, {"NPM": ""}, "tes")
        self.assertEqual((npm, filled), ("", {}))

    def test_missing_field_is_a_no_op(self):
        npm, filled = _cnn_rescue_npm(np.zeros((10, 10), np.uint8), {}, {"NPM": " "}, "tes")
        self.assertEqual((npm, filled), (" ", {}))


class TestCnnRescueDigitsGeneric(unittest.TestCase):
    """_cnn_rescue_digits generalisasi _cnn_rescue_npm -- di sini diuji dgn KODE SOAL (struktur identik NPM)."""

    @classmethod
    def setUpClass(cls):
        cls.fields = {"KODE SOAL": load_default_template()["fields"]["KODE SOAL"]}

    def test_fully_resolved_kode_soal_short_circuits_without_touching_model(self):
        called = []
        orig = svc.read_missing_npm_digits
        svc.read_missing_npm_digits = lambda *a, **k: called.append(1) or {}
        try:
            val, filled = _cnn_rescue_digits(
                np.zeros((10, 10), np.uint8), self.fields, {"KODE SOAL": "025"}, "tes", "KODE SOAL")
        finally:
            svc.read_missing_npm_digits = orig
        self.assertEqual((val, filled, called), ("025", {}, []))

    def test_missing_field_is_a_no_op(self):
        val, filled = _cnn_rescue_digits(np.zeros((10, 10), np.uint8), {}, {"KODE SOAL": " "}, "tes", "KODE SOAL")
        self.assertEqual((val, filled), (" ", {}))


class TestCnnRescueChoice(unittest.TestCase):
    """_cnn_rescue_choice: sama spt _cnn_rescue_digits tapi utk field pilihan-tunggal 1-kolom (FAKULTAS)."""

    @classmethod
    def setUpClass(cls):
        cls.fields = {"FAKULTAS": load_default_template()["fields"]["FAKULTAS"]}

    def test_complete_fakultas_short_circuits_without_touching_model(self):
        called = []
        orig = svc.read_missing_npm_digits
        svc.read_missing_npm_digits = lambda *a, **k: called.append(1) or {}
        try:
            val, filled = _cnn_rescue_choice(
                np.zeros((10, 10), np.uint8), self.fields, {"FAKULTAS": "FIF"}, "tes", "FAKULTAS")
        finally:
            svc.read_missing_npm_digits = orig
        self.assertEqual((val, filled, called), ("FIF", {}, []))

    def test_blank_fakultas_uses_cnn_result_when_confident(self):
        orig = svc.read_missing_npm_digits
        svc.read_missing_npm_digits = lambda *a, **k: {0: "FTE"}
        try:
            val, filled = _cnn_rescue_choice(
                np.zeros((10, 10), np.uint8), self.fields, {"FAKULTAS": "BLANK"}, "tes", "FAKULTAS")
        finally:
            svc.read_missing_npm_digits = orig
        self.assertEqual((val, filled), ("FTE", {0: "FTE"}))

    def test_missing_field_is_a_no_op(self):
        val, filled = _cnn_rescue_choice(np.zeros((10, 10), np.uint8), {}, {"FAKULTAS": "BLANK"}, "tes", "FAKULTAS")
        self.assertEqual((val, filled), ("BLANK", {}))


if __name__ == "__main__":
    unittest.main()
