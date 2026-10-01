"""Tes core.decoder.decode_field: pemetaan status evaluate_question ("OK"/"BLANK"/"MULTIPLE") ke nilai
akhir per pertanyaan utk blok multiple_choice (soal/kuisioner). evaluate_question di-mock supaya tes
independen dari kualitas gambar/fill-ratio sungguhan -- yang diuji di sini murni pemetaan statusnya."""
import unittest
from unittest.mock import patch

import numpy as np

from core.decoder import decode_field


def _field(n_items):
    items = []
    for i in range(1, n_items + 1):
        items.append({"index": i, "name": f"soal_{i:02d}", "bubbles": [
            {"cx": 10.0, "cy": 10.0, "radius": 5, "option": "A"},
            {"cx": 20.0, "cy": 10.0, "radius": 5, "option": "B"},
        ]})
    return {"field_name": "Soal-A", "field_type": "multiple_choice", "items": items}


class TestDecodeFieldMultipleChoice(unittest.TestCase):
    def setUp(self):
        self.gray = np.full((40, 40), 240, dtype=np.uint8)

    def test_multiple_marked_becomes_blank_not_sentinel(self):
        """Dobel-isi (dua kotak tersilang sekaligus) dianggap BLANK -- dulu disimpan sbg "MULTIPLE" (ikut
        dinilai SALAH krn bukan huruf jawaban yg valid), sekarang diperlakukan sama spt tak dijawab."""
        with patch("core.decoder.evaluate_question", return_value=(-1, "MULTIPLE")):
            out = decode_field(self.gray, _field(n_items=1))
        self.assertEqual(out["soal_01"], "BLANK")

    def test_blank_stays_blank_and_ok_reads_the_marked_option(self):
        with patch("core.decoder.evaluate_question", side_effect=[(-1, "BLANK"), (1, "OK")]):
            out = decode_field(self.gray, _field(n_items=2))
        self.assertEqual(out["soal_01"], "BLANK")
        self.assertEqual(out["soal_02"], "B")


if __name__ == "__main__":
    unittest.main()
