"""
Unit and Integration Tests for OMR Pipeline.
"""

import unittest
import json
import os
import cv2
import numpy as np

from omr.pipeline import process_ljk
from tests.generate_synthetic import generate_student_ljk, create_blank_canonical_ljk, fill_bubble
from utils.export import export_to_csv, export_to_excel


class TestOMRPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "default_template.json")
        with open(template_path, "r") as f:
            cls.template = json.load(f)

    def test_canonical_perfect_student(self):
        """Test exact deterministic decoding of a perfect student submission."""
        name_expected = "WAHYU"
        npm_expected = "1301234567"
        faculty_idx = 0  # FIF
        survey_expected = ["A", "B", "C", "D", "A", "B", "C", "D", "A", "B"]
        math_expected = [["A", "B", "C", "D"][i % 4] for i in range(100)]

        img = generate_student_ljk(
            self.template,
            name=name_expected,
            npm=npm_expected,
            faculty_index=faculty_idx,
            survey_answers=survey_expected,
            math_answers=math_expected,
            warp_skew=False
        )

        res = process_ljk(img, self.template, filename="IMG_PERFECT.jpg")

        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["name"], name_expected)
        self.assertEqual(res["npm"], npm_expected)
        self.assertTrue(res["faculty"].startswith("FIF"))
        self.assertEqual(res["survey"]["valid_count"], 10)
        self.assertEqual(res["math"]["valid_count"], 100)
        self.assertGreater(res["confidence"], 0.85)

        # Check math Q01 to Q100
        for i in range(1, 101):
            k = f"Q{i:02d}" if i < 100 else "Q100"
            self.assertEqual(res["math"]["answers"][k], math_expected[i - 1], f"Mismatch at {k}")

    def test_skewed_image_alignment(self):
        """Test alignment and decoding under camera rotation/perspective skew."""
        name_expected = "ANDI"
        npm_expected = "1301234568"
        faculty_idx = 4  # FIK

        img = generate_student_ljk(
            self.template,
            name=name_expected,
            npm=npm_expected,
            faculty_index=faculty_idx,
            warp_skew=True
        )

        res = process_ljk(img, self.template, filename="IMG_SKEWED.jpg")

        self.assertEqual(res["alignment"]["status"], "OK")
        self.assertEqual(res["name"], name_expected)
        self.assertEqual(res["npm"], npm_expected)
        self.assertTrue(res["faculty"].startswith("FIK"))

    def test_ambiguous_marking_detection(self):
        """Test that dual-shaded / multiple marked questions trigger NEEDS_REVIEW fail-safely."""
        img = generate_student_ljk(self.template, name="SITI", npm="1301234569", faculty_index=2)

        # Double-shade Math Question 1 (both A and B)
        m_cfg = self.template["mathematics"]["columns"][0]
        fill_bubble(img, m_cfg["start_x"] + 0 * m_cfg["choice_spacing_x"], m_cfg["start_y"])  # A
        fill_bubble(img, m_cfg["start_x"] + 1 * m_cfg["choice_spacing_x"], m_cfg["start_y"])  # B

        res = process_ljk(img, self.template, filename="IMG_AMBIGUOUS.jpg")
        self.assertEqual(res["status"], "NEEDS_REVIEW")
        self.assertGreaterEqual(res["math"]["ambiguous_count"], 1)

    def test_quality_gate_rejection_on_invalid_image(self):
        """Test that non-LJK or solid images fail safely without crashing."""
        blank_white = np.full((1000, 800, 3), 255, dtype=np.uint8)
        res = process_ljk(blank_white, self.template, filename="BLANK.jpg")
        self.assertEqual(res["status"], "FAILED")

    def test_export_functionality(self):
        """Test CSV and Excel generation integrity."""
        img = generate_student_ljk(self.template, name="BUDI", npm="1301234599", faculty_index=1)
        res = process_ljk(img, self.template, filename="IMG_BUDI.jpg")

        csv_str = export_to_csv([res])
        self.assertIn("BUDI", csv_str)
        self.assertIn("1301234599", csv_str)

        excel_bytes = export_to_excel([res])
        self.assertGreater(len(excel_bytes), 1000)


if __name__ == "__main__":
    unittest.main()
