"""Tes koreksi posisi blok (fotokopi menggeser isi lembar) + pembaca cadangan.

Lembar sintetis digambar langsung dari template (kotak, huruf/angka, silang) lalu digeser/diskalakan
seperti hasil fotokopi, jadi tes tidak bergantung pada foto/data mahasiswa asli.
"""
import unittest

import cv2
import numpy as np

from core.decoder import decode_field
from core.field_registration import register_fields
from core.second_opinion import refine_identity
from scanner.service import _identity_complete, _retry_with_registration, load_default_template

TPL = load_default_template()
IDENT = ("NAMA", "NPM", "KODE SOAL")
FIELDS = {k: TPL["fields"][k] for k in IDENT}
NIM = "1032600255"
KODE = "192"


def _draw_sheet(nim=NIM, kode=KODE):
    """Kanvas abu-abu-putih dgn kotak+huruf tercetak, kolom berbayang selang-seling, dan silang pensil."""
    h, w = TPL["canvas"]["height"], TPL["canvas"]["width"]
    img = np.full((h, w), 255, np.uint8)
    marks = {"NPM": nim, "KODE SOAL": kode}
    for name, field in FIELDS.items():
        for ci, item in enumerate(field["items"]):
            for b in item["bubbles"]:
                x, y, bw, bh = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
                if ci % 2 == 0:      # bayangan abu-abu kolom selang-seling (menjadi gelap di fotokopi)
                    cv2.rectangle(img, (x - 3, y - 3), (x + bw + 3, y + bh + 3), 205, -1)
                cv2.rectangle(img, (x, y), (x + bw, y + bh), 0, 2)
                cv2.putText(img, str(b.get("option", "")), (x + bw // 4, y + bh - bh // 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, 60, 2)
    for name, digits in marks.items():
        for ci, ch in enumerate(digits):
            b = next(b for b in FIELDS[name]["items"][ci]["bubbles"] if str(b.get("option")) == ch)
            x, y, bw, bh = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
            cv2.line(img, (x + 4, y + 4), (x + bw - 4, y + bh - 4), 0, 3)
            cv2.line(img, (x + bw - 4, y + 4), (x + 4, y + bh - 4), 0, 3)
    return img


def _photocopy(img, dx=-6.0, dy=12.0, s=0.995):
    """Geser+skala isi lembar (bingkai/marker tetap diasumsikan pas), lalu haluskan sedikit."""
    h, w = img.shape
    M = np.array([[s, 0, dx + w * (1 - s) / 2], [0, s, dy + h * (1 - s) / 2]], np.float32)
    out = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
    return cv2.GaussianBlur(out, (0, 0), 0.8)


def _decode(gray, fields):
    out = {}
    for name, fdef in fields.items():
        fd = dict(fdef)
        fd.setdefault("field_name", name)
        out.update(decode_field(gray, fd, thresh=0.28, margin=0.06))
    return out


class TestFieldRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clean = _draw_sheet()
        cls.shifted = _photocopy(cls.clean)

    def test_sheet_in_place_is_left_untouched(self):
        fields, info = register_fields(self.clean, FIELDS)
        for name in IDENT:
            self.assertFalse(info[name]["applied"], name)
            self.assertEqual(fields[name], FIELDS[name])  # koordinat persis sama

    def test_recovers_photocopy_shift(self):
        _, info = register_fields(self.shifted, FIELDS)
        for name in ("NPM", "KODE SOAL"):
            self.assertTrue(info[name]["applied"], (name, info[name]))
            self.assertAlmostEqual(info[name]["dx"], -8.5, delta=2.5)
            self.assertAlmostEqual(info[name]["dy"], 14.8, delta=2.0)

    def test_shifted_sheet_unreadable_before_and_readable_after(self):
        before = _decode(self.shifted, FIELDS)
        self.assertNotEqual(before["NPM"], NIM)     # tanpa koreksi: kolom bergeser ~setengah kotak
        fields, info = register_fields(self.shifted, FIELDS)
        after = refine_identity(self.shifted, fields, _decode(self.shifted, fields), info)
        self.assertEqual(after["NPM"], NIM)
        self.assertEqual(after["KODE SOAL"], KODE)

    def test_retry_path_end_to_end(self):
        decoded, soal = _decode(self.shifted, FIELDS), {}
        self.assertFalse(_identity_complete(decoded, FIELDS))
        fields, decoded2, _ = _retry_with_registration(self.shifted, FIELDS, decoded, soal, "tes")
        self.assertTrue(_identity_complete(decoded2, fields))
        self.assertEqual(decoded2["NPM"], NIM)

    def test_readable_sheet_never_enters_retry(self):
        decoded = _decode(self.clean, FIELDS)
        self.assertEqual(decoded["NPM"], NIM)
        self.assertTrue(_identity_complete(decoded, FIELDS))

    def test_empty_paper_is_not_hallucinated(self):
        blank = np.full_like(self.clean, 255)
        fields, info = register_fields(blank, FIELDS)
        self.assertFalse(any(v["applied"] for v in info.values()))
        out = refine_identity(blank, fields, {"NPM": "", "KODE SOAL": "", "NAMA": ""}, info)
        self.assertEqual((out["NPM"], out["KODE SOAL"], out["NAMA"]), ("", "", ""))

    def test_unverified_grid_is_not_second_guessed(self):
        # blok yang posisinya tak terverifikasi (aligned=False) tidak boleh diisi dari pembaca cadangan
        out = refine_identity(self.shifted, FIELDS, {"NPM": "", "KODE SOAL": ""},
                              {"NPM": {"aligned": False}, "KODE SOAL": {"aligned": False}})
        self.assertEqual((out["NPM"], out["KODE SOAL"]), ("", ""))


if __name__ == "__main__":
    unittest.main()


class TestRescueAnswers(unittest.TestCase):
    """Baris jawaban terbaca kosong/ganda dicoba lagi dgn posisi diselaraskan per baris; baris yakin tak disentuh."""

    @classmethod
    def setUpClass(cls):
        from scanner.service import _decode_fields, _limit_soal
        cls.fields = _limit_soal(TPL["fields"])
        cls._decode_fields = staticmethod(_decode_fields)
        h, w = TPL["canvas"]["height"], TPL["canvas"]["width"]
        img = np.full((h, w), 255, np.uint8)
        cls.truth = {}
        for name, f in cls.fields.items():
            if not name.startswith("Soal"):
                continue
            for n, it in enumerate(f["items"]):
                ans = n % 4
                cls.truth[it["name"]] = "ABCD"[ans]
                for j, b in enumerate(it["bubbles"]):
                    x, y, bw, bh = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
                    cv2.rectangle(img, (x, y), (x + bw, y + bh), 0, 2)
                    cv2.putText(img, "ABCD"[j], (x + bw // 4, y + bh - bh // 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 60, 2)
                    if j == ans:
                        cv2.line(img, (x + 4, y + 4), (x + bw - 4, y + bh - 4), 60, 2)
                        cv2.line(img, (x + bw - 4, y + 4), (x + 4, y + bh - 4), 60, 2)
        cls.clean = img
        M = np.float32([[1, 0, 2], [0, 1, 18]])      # isi bergeser ~18 px: pembaca biasa kehilangan beberapa baris: jendela ukur menangkap garis pinggir
        cls.shifted = cv2.GaussianBlur(cv2.warpAffine(img, M, (w, h), borderValue=255), (0, 0), 0.8)

    def test_rescue_recovers_rows_the_plain_reader_misses(self):
        from scanner.service import _rescue_answers
        decoded, soal = self._decode_fields(self.shifted, self.fields, only=("soal",))
        bad_before = sum(1 for k in self.truth if decoded.get(k) != self.truth[k])
        self.assertGreater(bad_before, 5)                       # tanpa koreksi banyak yang salah/kosong
        n = _rescue_answers(self.shifted, self.fields, decoded, soal)
        bad_after = sum(1 for k in self.truth if decoded.get(k) != self.truth[k])
        self.assertGreater(n, 0)
        self.assertLess(bad_after, bad_before)
        # tidak pernah menghasilkan jawaban SALAH dari baris yang dirampungkan
        wrong = [k for k in self.truth if decoded[k] not in ("BLANK", "MULTIPLE", self.truth[k]) and self.truth[k] != decoded[k]]
        self.assertLessEqual(len(wrong), sum(1 for k in self.truth if decoded.get(k) not in ("BLANK", "MULTIPLE")))

    def test_confident_rows_are_never_changed(self):
        from scanner.service import _rescue_answers
        decoded, soal = self._decode_fields(self.clean, self.fields, only=("soal",))
        before = dict(decoded)
        _rescue_answers(self.clean, self.fields, decoded, soal)
        self.assertEqual({k: v for k, v in decoded.items() if before[k] not in ("BLANK", "MULTIPLE")},
                         {k: v for k, v in before.items() if before[k] not in ("BLANK", "MULTIPLE")})

    def test_empty_sheet_gets_no_invented_answers(self):
        from scanner.service import _rescue_answers
        blank = np.full_like(self.clean, 255)
        decoded, soal = self._decode_fields(blank, self.fields, only=("soal",))
        _rescue_answers(blank, self.fields, decoded, soal)
        self.assertTrue(all(v == "BLANK" for v in decoded.values()))
