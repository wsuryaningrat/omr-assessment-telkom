"""Regression: hasil pemindaian harus identik dengan golden (direkam dari versi Streamlit).

Jalankan:        python -m unittest tests.regression.test_scan_regression
Rekam ulang:     python -m tests.regression.test_scan_regression --regen   (hanya bila perubahan hasil disengaja)
"""
import io
import json
import os
import sys
import unittest

import cv2

from core.pdf_utils import iter_images_from_file
from scanner.service import SCAN_MAX_SIDE, load_default_template, scan_page
from tests.regression.fixtures import KUNCI, build_cases

GOLDEN = os.path.join(os.path.dirname(__file__), "golden_scan.json")
PENGAWAS = {"hp": "0812", "ruangan": "R1", "prodi": "S1 Informatika"}


class _Upload:
    def __init__(self, name, data):
        self.name, self._data = name, data

    def getvalue(self):
        return self._data


def compute():
    tpl = load_default_template()
    cases = build_cases()
    images = dict(cases)
    # Jalur produksi: foto JPEG -> decode (SCAN_MAX_SIDE, default asli) -> pindai
    ok, buf = cv2.imencode(".jpg", cases["filled_phone"], [cv2.IMWRITE_JPEG_QUALITY, 90])
    _, decoded = next(iter_images_from_file(_Upload("foto.jpg", buf.tobytes()), max_side=SCAN_MAX_SIDE))
    images["filled_phone_jpeg_pipeline"] = decoded

    out = {}
    for name, img in images.items():
        rec, prev = scan_page(img, name, tpl, "FIF - Fakultas Informatika", "Budi", KUNCI, PENGAWAS)
        rec = dict(rec)
        rec.pop("Submit Date", None)  # bergantung waktu
        out[name] = {"record": rec, "status": prev["status"], "method": prev["method"]}
    return out


class TestScanRegression(unittest.TestCase):
    def test_matches_golden(self):
        with open(GOLDEN, encoding="utf-8") as f:
            golden = json.load(f)
        current = json.loads(json.dumps(compute(), ensure_ascii=False))
        self.assertEqual(set(golden), set(current))
        for name in golden:
            with self.subTest(case=name):
                self.assertEqual(golden[name]["status"], current[name]["status"])
                self.assertEqual(golden[name]["record"], current[name]["record"])


if __name__ == "__main__":
    if "--regen" in sys.argv:
        with open(GOLDEN, "w", encoding="utf-8") as f:
            json.dump(compute(), f, ensure_ascii=False, indent=1, sort_keys=True)
        print("golden ditulis:", GOLDEN)
    else:
        unittest.main()
