"""Hanya soal 1..MAX_SOAL (50) yang dibaca & dinilai; kotak jawaban 51-75 diabaikan."""
import re
import unittest

from core.evaluator import MAX_SOAL, grade_student_record
from scanner.service import _limit_soal, load_default_template

TPL = load_default_template()


class TestSoalLimit(unittest.TestCase):
    def test_default_limit_is_50(self):
        self.assertEqual(MAX_SOAL, 50)

    def test_answer_blocks_are_trimmed(self):
        fields = _limit_soal(TPL["fields"])
        nums = [int(re.search(r"(\d+)$", it["name"]).group(1))
                for n, f in fields.items() if n.startswith("Soal") for it in f["items"]]
        self.assertEqual(sorted(nums), list(range(1, 51)))
        self.assertNotIn("Soal-E", fields)          # blok 61-75 habis -> dibuang total

    def test_non_answer_fields_untouched(self):
        fields = _limit_soal(TPL["fields"])
        for name in ("NAMA", "NPM", "KODE SOAL", "FAKULTAS", "Kuisioner-A", "Kuisioner-B", "Kuisioner-C"):
            self.assertIs(fields[name], TPL["fields"][name], name)

    def test_grading_ignores_key_rows_beyond_50(self):
        kunci = {"A": {i: "ABCD"[i % 4] for i in range(1, 76)}}      # kunci 75 baris
        rec = {"Kode Soal": "A"}
        rec.update({f"soal_{i:02d}": "ABCD"[i % 4] for i in range(1, 51)})   # 50 jawaban benar semua
        graded = grade_student_record(dict(rec), kunci)
        self.assertEqual(graded["Total Soal"], 50)
        self.assertEqual(graded["Jumlah Benar"], 50)
        self.assertEqual(graded["Jumlah Kosong"], 0)
        self.assertEqual(graded["Nilai"], "100")


if __name__ == "__main__":
    unittest.main()
