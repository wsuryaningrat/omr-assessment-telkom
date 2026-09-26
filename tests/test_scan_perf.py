import unittest

import numpy as np

from core import alignment
from server.worker import threads_for


class TestThreadsFor(unittest.TestCase):
    def test_adaptif_mengikuti_beban(self):
        self.assertEqual(threads_for(1, 4), 4)     # sepi: semua core untuk satu foto
        self.assertEqual(threads_for(2, 4), 2)
        self.assertEqual(threads_for(3, 4), 1)
        self.assertEqual(threads_for(4, 4), 1)     # penuh: 1 thread per worker
        self.assertEqual(threads_for(40, 4), 1)
        self.assertEqual(threads_for(0, 4), 4)

    def test_nonaktif_memakai_batas_bawah(self):
        self.assertEqual(threads_for(1, 4, adaptive=False), 1)
        self.assertEqual(threads_for(1, 4, adaptive=False, floor=2), 2)


class TestDetectEnhance(unittest.TestCase):
    def setUp(self):
        import cv2
        from tests.regression.test_scan_regression import build_cases
        self.gray = cv2.cvtColor(build_cases()["filled_phone"], cv2.COLOR_BGR2GRAY)
        self.gray = cv2.resize(self.gray, (1650, 2200), interpolation=cv2.INTER_AREA)

    def test_default_penuh_tidak_berubah(self):
        a = alignment.enhance_scan_gray(self.gray)
        b = alignment.enhance_scan_gray(self.gray, bg_downscale=1)
        self.assertTrue(np.array_equal(a, b))

    def test_downscale_dekat_dengan_penuh(self):
        a = alignment.enhance_scan_gray(self.gray).astype(int)
        b = alignment.enhance_scan_gray(self.gray, bg_downscale=2).astype(int)
        d = np.abs(a - b)
        self.assertLess(d.mean(), 1.0)      # terukur ±0,2 pada foto simulasi HP
        self.assertLess(d.max(), 24)

    def test_bgr_default_penuh_tidak_berubah(self):
        self.assertGreaterEqual(alignment.DETECT_BG_DOWNSCALE, 1)
        bgr = np.dstack([self.gray] * 3)
        x = alignment.enhance_scan_bgr(bgr)
        y = alignment.enhance_scan_bgr(bgr, bg_downscale=1)
        self.assertTrue(np.array_equal(x, y))


if __name__ == "__main__":
    unittest.main()
