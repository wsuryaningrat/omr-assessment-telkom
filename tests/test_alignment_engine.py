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
