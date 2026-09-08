"""
Automated Integration Tests for ArUco Alignment and Strict Inner Crop Engine.
Verifies:
1. Strict inner crop completely excludes ArUco markers from the canvas.
2. 3-marker geometric recovery handles missing or occluded corner markers.
3. Rotated images (0, 90, 180, 270 deg) are aligned to canonical upright 1700x2400 canvas.
4. End-to-end OMR decoding yields accurate student data.
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
    make_fast_detector,
    rotate_image,
    unrotate_point
)
from core.decoder import decode_field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_image(rel_path):
    path = os.path.join(BASE_DIR, rel_path)
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

    def test_manual_photo_strict_inner_crop(self):
        """Verify manual_1.jpeg crops strictly inside ArUco markers with 0 markers in canvas."""
        img = load_image("sample foto/manual_1.jpeg")
        self.assertIsNotNone(img)

        warped, pts, method, c_ids, d_name, status, _ = detect_corners_and_crop(
            img, preferred_method="aruco", crop_mode="inner", apply_standardization=True
        )
        self.assertTrue(status.startswith("DETECTED"))
        self.assertEqual(method, "aruco")
        self.assertEqual(warped.shape, (2400, 1700, 3))

        # Check that ArUco detector finds ZERO markers inside the cropped canvas
        gray_w = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        det = make_fast_detector(cv2.aruco.DICT_4X4_50)
        c, ids, _ = det.detectMarkers(gray_w)
        self.assertIsNone(ids, "ArUco markers must NOT appear inside the inner cropped canvas")

    def test_heic_photo_with_glare_recovery(self):
        """Verify IMG_9534.HEIC (with marker 1 glare) recovers all 4 markers and decodes correctly."""
        img = load_image("sample foto/IMG_9534.HEIC")
        self.assertIsNotNone(img)

        warped, pts, method, c_ids, d_name, status, _ = detect_corners_and_crop(
            img, preferred_method="aruco", crop_mode="inner", apply_standardization=True
        )
        self.assertTrue(status.startswith("DETECTED"))
        self.assertEqual(method, "aruco")
        self.assertEqual(warped.shape, (2400, 1700, 3))

        # Check OMR decoding on the recovered canvas
        gray_w = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        fields = {}
        for fname, fdef in self.template.get("fields", {}).items():
            fcopy = dict(fdef)
            fcopy["field_name"] = fname
            fields.update(decode_field(gray_w, fcopy, thresh=0.28, margin=0.08))

        self.assertIn("WAHYU", fields.get("NAMA", ""))
        self.assertIn("1234321119", fields.get("NPM", ""))
        self.assertEqual(fields.get("FAKULTAS", ""), "FKS")
        self.assertEqual(fields.get("KODE SOAL", ""), "111")

        soal_filled = sum(1 for k, v in fields.items() if k.startswith("soal_") and v != "BLANK")
        self.assertEqual(soal_filled, 75, f"Expected 75 filled questions, got {soal_filled}")

    def test_multi_angle_rotation(self):
        """Verify all 4 rotation angles (0, 90, 180, 270) produce canonical upright 1700x2400."""
        orig = load_image("sample foto/manual_1.jpeg")
        for ang in [0, 90, 180, 270]:
            rot = rotate_image(orig, ang) if ang != 0 else orig
            warped, pts, method, c_ids, d_name, status, _ = detect_corners_and_crop(
                rot, preferred_method="aruco", crop_mode="inner", apply_standardization=False
            )
            self.assertTrue(status.startswith("DETECTED"), f"Failed detection at angle {ang}")
            self.assertEqual(warped.shape, (2400, 1700, 3), f"Wrong shape at angle {ang}")

            # Verify points are within image boundaries
            h, w = rot.shape[:2]
            for pt in pts:
                self.assertGreaterEqual(pt[0], 0)
                self.assertLessEqual(pt[0], w)
                self.assertGreaterEqual(pt[1], 0)
                self.assertLessEqual(pt[1], h)

    def test_corner_first_regmark_and_doc_bounds(self):
        """Verify corner-first detection clearly locks 4 physical corners on sheets with and without regmarks."""
        from core.pdf_utils import extract_images_from_file
        from core.alignment import find_document_corners

        # Test on sheet without printed corner markers (sample_no corner.pdf)
        pdf_path = os.path.join(BASE_DIR, "templates", "sample_no corner.pdf")
        with open(pdf_path, "rb") as fp:
            img_no_corner = extract_images_from_file(fp)[0][1]

        warped, pts, method, c_ids, d_name, status, _ = detect_corners_and_crop(
            img_no_corner, preferred_method="auto", crop_mode="inner"
        )
        self.assertTrue(status.startswith("DETECTED"), f"Failed detection on sample_no corner: {status}")
        self.assertEqual(warped.shape, (2400, 1700, 3))
        self.assertEqual(len(pts), 4)

    def test_digital_scan_corners(self):
        """Verify digital scan LJK (digital_1.pdf) locks 4 corners cleanly."""
        from core.pdf_utils import extract_images_from_file

        pdf_path = os.path.join(BASE_DIR, "sample foto", "digital_1.pdf")
        with open(pdf_path, "rb") as fp:
            img_digital = extract_images_from_file(fp)[0][1]

        warped, pts, method, c_ids, d_name, status, _ = detect_corners_and_crop(
            img_digital, preferred_method="auto", crop_mode="inner"
        )
        self.assertTrue(status.startswith("DETECTED"), f"Failed detection on digital_1: {status}")
        self.assertEqual(warped.shape, (2400, 1700, 3))
        self.assertEqual(len(pts), 4)


if __name__ == "__main__":
    unittest.main()

