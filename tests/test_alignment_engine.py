"""
Automated Integration Tests for Green Frame Alignment, ArUco Registration, and Bubble Delta Validation.
Verifies:
1. Mathematical exactness of geometric unrotate.
2. Deterministic Green Frame crop & perspective normalization on baseline reference (IMG_9558.HEIC).
3. Robust Green Frame recovery & alignment consistency on challenging scan (IMG_9557.HEIC).
4. ArUco serves strictly as registration/validation reference in normalized LJK space.
5. Bubble delta evaluation demonstrates aligned coordinate spaces (std < 3.5 px).
"""

import unittest
import os
import json
import cv2
import numpy as np
from PIL import Image
import pillow_heif
pillow_heif.register_heif_opener()

from core.alignment import (
    detect_corners_and_crop,
    rotate_image,
    unrotate_point
)
from core.decoder import decode_field
from core.utils import evaluate_template_bubble_alignment

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_image(rel_path):
    path = os.path.join(BASE_DIR, rel_path)
    if not os.path.exists(path):
        return None
    if path.lower().endswith((".heic", ".heif")):
        pil_img = Image.open(path)
        return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return cv2.imread(path)


class TestAlignmentEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        template_path = os.path.join(BASE_DIR, "template-final.json")
        with open(template_path, "r") as f:
            cls.template = json.load(f)

    def test_unrotate_geometry(self):
        """Verify unrotate_point is mathematically exact for 90, 180, 270 deg rotations."""
        h, w = 600, 800
        pt_orig = np.array([175.0, 320.0], dtype=np.float32)
        for ang in [90, 180, 270]:
            img = np.zeros((h, w, 3), dtype=np.uint8)
            img[int(pt_orig[1]), int(pt_orig[0])] = [0, 255, 0]
            rot = rotate_image(img, ang)
            ys, xs = np.where(rot[:, :, 1] == 255)
            rx, ry = xs[0], ys[0]
            unrot = unrotate_point((rx, ry), (h, w), ang)
            self.assertLess(np.max(np.abs(unrot - pt_orig)), 1.0)

    def test_reference_sample_alignment_and_decode(self):
        """Verify baseline reference (IMG_9558.HEIC) produces canonical 1700x2400 and accurate student data."""
        img = load_image("sample foto/IMG_9558.HEIC")
        self.assertIsNotNone(img, "IMG_9558.HEIC must exist in sample foto/")

        warped, pts, method, c_ids, d_name, status, reg = detect_corners_and_crop(
            img, preferred_method="green_frame", apply_standardization=True
        )
        self.assertTrue(status.startswith("DETECTED"), f"Expected DETECTED, got {status}")
        self.assertEqual(method, "green_frame")
        self.assertEqual(warped.shape, (2400, 1700, 3))

        gray_w = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        fields = {}
        for fname, fdef in self.template.get("fields", {}).items():
            fcopy = dict(fdef)
            fcopy["field_name"] = fname
            fields.update(decode_field(gray_w, fcopy, thresh=0.28, margin=0.08))

        self.assertIn("WAHYU", fields.get("NAMA", ""))
        self.assertEqual(fields.get("NPM", ""), "1233322566")
        self.assertEqual(fields.get("FAKULTAS", ""), "FIK")

        # Evaluate coordinate alignment
        summary, deltas = evaluate_template_bubble_alignment(gray_w, self.template.get("fields", {}))
        self.assertIn(summary.get("quality"), ("EXCELLENT", "ALIGNED"))
        self.assertLess(summary.get("std_dx", 99), 3.5)
        self.assertLess(summary.get("std_dy", 99), 3.5)

    def test_problematic_sample_recovery_and_alignment(self):
        """Verify challenging scan (IMG_9557.HEIC) normalizes into same canonical space with matching answers."""
        img = load_image("sample foto/IMG_9557.HEIC")
        self.assertIsNotNone(img, "IMG_9557.HEIC must exist in sample foto/")

        warped, pts, method, c_ids, d_name, status, reg = detect_corners_and_crop(
            img, preferred_method="green_frame", apply_standardization=True
        )
        self.assertTrue(status.startswith("DETECTED"), f"Expected DETECTED, got {status}")
        self.assertEqual(method, "green_frame")
        self.assertEqual(warped.shape, (2400, 1700, 3))

        gray_w = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        fields = {}
        for fname, fdef in self.template.get("fields", {}).items():
            fcopy = dict(fdef)
            fcopy["field_name"] = fname
            fields.update(decode_field(gray_w, fcopy, thresh=0.28, margin=0.08))

        # Must match reference decoded identity
        self.assertIn("WAHYU", fields.get("NAMA", ""))
        self.assertEqual(fields.get("NPM", ""), "1233322566")
        self.assertEqual(fields.get("FAKULTAS", ""), "FIK")

        # Evaluate coordinate alignment: delta must be tight without large perspective skew
        summary, deltas = evaluate_template_bubble_alignment(gray_w, self.template.get("fields", {}))
        self.assertIn(summary.get("quality"), ("EXCELLENT", "ALIGNED"))
        self.assertLess(summary.get("std_dx", 99), 3.5)
        self.assertLess(summary.get("std_dy", 99), 3.5)
        self.assertLess(abs(summary.get("median_dx", 99)), 3.0)
        self.assertLess(abs(summary.get("median_dy", 99)), 3.0)

    def test_aruco_registration_reference(self):
        """Verify ArUco markers serve strictly as validation reference metadata."""
        img = load_image("sample foto/IMG_9558.HEIC")
        warped, pts, method, c_ids, d_name, status, reg = detect_corners_and_crop(
            img, preferred_method="green_frame", apply_standardization=True
        )
        self.assertIsNotNone(reg)
        self.assertIn("normalized_centers", reg)
        self.assertIn("registration_quality", reg)
        self.assertIn(reg.get("registration_quality"), ("EXCELLENT", "VALID"))


if __name__ == "__main__":
    unittest.main()


class TestBlurredMarkerFallback(unittest.TestCase):
    """Marker sudut buram/terkompres (mis. foto WhatsApp) gagal di-decode: posisinya harus ditemukan lewat
    pencocokan gambar marker yang diharapkan, BUKAN ditebak (tebakan pernah meleset >100 px dan merusak seluruh crop)."""

    @classmethod
    def setUpClass(cls):
        import cv2
        import numpy as np
        from core.alignment import detect_corners_and_crop
        from scanner.service import load_default_template
        from tests.regression.fixtures import build_cases
        cls.cv2, cls.np, cls.detect = cv2, np, staticmethod(detect_corners_and_crop)
        cls.tpl = load_default_template()
        cls.base = build_cases()["filled_phone"]
        cls.reg0 = cls._run(cls.base)
        box = cls.reg0["marker_boxes"]["BL"]
        cls.true_bl = cls.reg0["marker_centers"]["BL"]
        cls.x0, cls.y0 = (box.min(axis=0) - 15).astype(int)
        cls.x1, cls.y1 = (box.max(axis=0) + 15).astype(int)
        cls.side = float(np.sqrt(abs(cv2.contourArea(box.reshape(-1, 1, 2)))))

    @classmethod
    def _run(cls, img):
        t = cls.tpl
        out = cls.detect(img, canvas_w=t["canvas"]["width"], canvas_h=t["canvas"]["height"], preferred_method="aruco",
                         expected_ids=t.get("aruco_corner_ids"), dict_name=t.get("aruco_dict", "DICT_4X4_50"), crop_mode="inner")
        return out[6]

    def _blurred(self, sigma=8):
        cv2, img = self.cv2, self.base.copy()
        roi = img[self.y0:self.y1, self.x0:self.x1]
        small = cv2.resize(roi, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        img[self.y0:self.y1, self.x0:self.x1] = cv2.resize(small, (roi.shape[1], roi.shape[0]), interpolation=cv2.INTER_LINEAR)
        img[self.y0:self.y1, self.x0:self.x1] = cv2.GaussianBlur(img[self.y0:self.y1, self.x0:self.x1], (0, 0), sigma)
        return img

    def test_template_search_locates_undecodable_marker(self):
        from core.alignment import _template_marker_search
        gray = self.cv2.cvtColor(self._blurred(), self.cv2.COLOR_BGR2GRAY)
        guess = self.true_bl + self.np.array([70.0, -50.0])            # tebakan ekstrapolasi yang meleset
        found = _template_marker_search(gray, self.cv2.aruco.DICT_4X4_50, 2, guess, 220, self.side)
        self.assertIsNotNone(found)
        self.assertLess(float(self.np.linalg.norm(found.mean(axis=0) - self.true_bl)), 8.0)

    def test_pipeline_uses_real_position_not_extrapolation(self):
        reg = self._run(self._blurred())
        self.assertLess(float(self.np.linalg.norm(reg["marker_centers"]["BL"] - self.true_bl)), 8.0)

    def test_no_match_on_plain_paper(self):
        from core.alignment import _template_marker_search
        blank = self.np.full((900, 900), 235, self.np.uint8)
        self.assertIsNone(_template_marker_search(blank, self.cv2.aruco.DICT_4X4_50, 2, (450, 450), 200, 30.0))


class TestAlignmentSpeedups(unittest.TestCase):
    """Percepatan perataan tidak boleh mengubah hasil: jalur marker cepat identik dgn jalur lengkap; blur kanvas terhitung dekat."""

    @classmethod
    def setUpClass(cls):
        from tests.regression.fixtures import build_cases
        cls.img = build_cases()["filled_phone"]        # foto ponsel sintetis (miring 3 derajat, 12 MP)

    def _markers(self, fast):
        import core.alignment as al
        old = al.FAST_MARKER
        al.FAST_MARKER = fast
        try:
            boxes, ids, _, status = al.find_aruco_markers(self.img)
        finally:
            al.FAST_MARKER = old
        self.assertEqual(status, "DETECTED")
        return boxes

    def test_fast_marker_path_matches_full_search(self):
        fast, full = self._markers(True), self._markers(False)
        for lbl in ("TL", "TR", "BR", "BL"):
            self.assertLess(float(np.abs(fast[lbl] - full[lbl]).max()), 0.01, lbl)

    def test_fast_marker_falls_back_when_probe_fails(self):
        # ROI kosong (tak ada marker) -> jalur lama tetap dijalankan dan melaporkan gagal, bukan error/tebakan
        import core.alignment as al
        blank = np.full((1200, 900, 3), 235, np.uint8)
        boxes, _, _, status = al.find_aruco_markers(blank)
        self.assertIsNone(boxes)
        self.assertTrue(status.startswith("ArUco: Ditemukan 0/4"))

    def test_canvas_background_downscale_stays_close(self):
        import core.alignment as al
        gray = cv2.cvtColor(self.img, cv2.COLOR_BGR2GRAY)[:2400, :1700]
        full = al.enhance_scan_gray(gray, 1.0, 1).astype(int)
        for f in (2, 3, 4):
            self.assertLess(float(np.abs(al.enhance_scan_gray(gray, 1.0, f).astype(int) - full).mean()), 1.5, f)
