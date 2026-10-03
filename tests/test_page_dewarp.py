"""Tes layer 1.5 (core.page_dewarp): koreksi kelengkungan non-planar kertas ("bergelombang") pada
kanvas yang sudah di-crop kasar. Lembar sintetis dipinjam dari tests.test_field_registration
(_draw_full_sheet) lalu didistorsi dengan medan pergeseran sinusoidal buatan (bukan cuma geser/skala
spt tes fotokopi affine) supaya benar-benar menguji koreksi lengkung, bukan sekadar translasi.
"""
import unittest

import cv2
import numpy as np

from core.decoder import decode_field
from core.field_registration import _cells
from core.page_dewarp import (
    _filter_local_outliers,
    _global_grid_contrast,
    _jacobian_ok,
    build_remap,
    dewarp_page,
    eval_tps,
    fit_tps,
    sample_control_points,
)
from tests.test_field_registration import ALL_FIELDS, NAMA_MARK, TPL, _draw_full_sheet

NAMA_MARK_TRIMMED = NAMA_MARK.rstrip()

CANVAS_H, CANVAS_W = TPL["canvas"]["height"], TPL["canvas"]["width"]


def _sinusoidal_warp(gray, amplitude=18.0, period_frac=0.9):
    """Distorsi non-planar buatan: geser horizontal tiap baris mengikuti gelombang sinus vertikal
    (mensimulasikan kertas melengkung sepanjang tinggi halaman). map_x/map_y dibangun manual (bukan
    lewat page_dewarp sendiri, supaya tes independen dari kode yang diuji)."""
    h, w = gray.shape
    ys = np.arange(h, dtype=np.float32)
    period = period_frac * h
    dx = amplitude * np.sin(2 * np.pi * ys / period)
    map_x = np.tile((np.arange(w, dtype=np.float32))[None, :], (h, 1)) - dx[:, None]
    map_y = np.tile(ys[:, None], (1, w))
    return cv2.remap(gray, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderValue=255)


def _decode(gray, field, name):
    fd = dict(field)
    fd.setdefault("field_name", name)
    return decode_field(gray, fd, thresh=0.28, margin=0.06)


class TestRemapDirection(unittest.TestCase):
    """Kelas bug paling berisiko: arah TPS harus template->detected (= arah yang cv2.remap butuhkan
    langsung, tanpa inversi). Kalau arahnya kebalik, marker akan mendarat di template + 2*offset,
    bukan kembali ke posisi template."""

    def test_map_direction_is_output_to_input(self):
        template_pts = np.array([[100, 100], [500, 100], [100, 400], [500, 400], [300, 250]], dtype=np.float64)
        offset = np.array([15.0, -8.0])
        detected_pts = template_pts + offset   # simulasi: isi ditemukan bergeser offset di citra
        tps = fit_tps(template_pts, detected_pts)
        self.assertIsNotNone(tps)
        mx, my = eval_tps(tps, np.array([300.0]), np.array([250.0]))
        # eval_tps(template_xy) harus ~= detected_xy (bukan template - offset, bukan template + 2*offset)
        self.assertAlmostEqual(float(mx[0]), 300.0 + offset[0], delta=0.5)
        self.assertAlmostEqual(float(my[0]), 250.0 + offset[1], delta=0.5)

        # Uji end-to-end lewat cv2.remap: taruh marker di posisi DETECTED pada citra sintetis,
        # remap harus memindahkannya ke posisi TEMPLATE (itulah tujuan dewarp: meluruskan kembali).
        img = np.full((512, 640), 255, np.uint8)
        mark_template_xy = (300, 250)
        mark_detected_xy = (int(300 + offset[0]), int(250 + offset[1]))
        cv2.circle(img, mark_detected_xy, 6, 0, -1)
        map_x, map_y = build_remap(tps, 640, 512)
        out = cv2.remap(img, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderValue=255)
        # Piksel gelap sekarang harus ada di sekitar posisi TEMPLATE, bukan di posisi awal maupun 2x offset
        window = out[mark_template_xy[1] - 8:mark_template_xy[1] + 8, mark_template_xy[0] - 8:mark_template_xy[0] + 8]
        self.assertLess(float(window.min()), 100, "marker tidak lurus kembali ke posisi template")


class TestControlPointSampling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clean, _ = _draw_full_sheet()

    def test_finds_many_points_on_clean_sheet(self):
        tpts, dpts, meta = sample_control_points(self.clean, ALL_FIELDS)
        self.assertGreaterEqual(len(tpts), 60)
        fields_covered = sum(1 for v in meta.values() if v >= 4)
        self.assertGreaterEqual(fields_covered, 6)
        # kertas bersih (tak didistorsi): titik terdeteksi harus dekat posisi templatenya sendiri
        disp = np.linalg.norm(dpts - tpts, axis=1)
        self.assertLess(float(np.median(disp)), 2.0)

    def test_single_column_field_contributes_nothing(self):
        tpts, dpts, meta = sample_control_points(self.clean, {"FAKULTAS": TPL["fields"]["FAKULTAS"]})
        self.assertEqual(len(tpts), 0)
        self.assertEqual(meta, {})


class TestOutlierFilter(unittest.TestCase):
    def test_bad_correspondence_is_dropped(self):
        rng = np.random.default_rng(0)
        template_pts = rng.uniform(0, 500, size=(30, 2))
        detected_pts = template_pts + rng.normal(0, 1.0, size=(30, 2))   # noise kecil, konsisten
        detected_pts[0] += 200.0   # satu titik salah total (mis. kisi meleset satu periode)
        kept_t, kept_d, n_dropped = _filter_local_outliers(template_pts, detected_pts)
        self.assertGreaterEqual(n_dropped, 1)
        self.assertNotIn(tuple(detected_pts[0]), [tuple(p) for p in kept_d])


class TestJacobianGate(unittest.TestCase):
    def test_near_identity_map_passes(self):
        h, w = 200, 300
        yy, xx = np.mgrid[0:h, 0:w]
        map_x, map_y = xx.astype(np.float32), yy.astype(np.float32)
        self.assertTrue(_jacobian_ok(map_x, map_y, edge_margin=10))

    def test_folding_map_is_rejected(self):
        h, w = 200, 300
        yy, xx = np.mgrid[0:h, 0:w]
        map_x = (-xx).astype(np.float32)   # cermin -> Jacobian negatif di mana-mana
        map_y = yy.astype(np.float32)
        self.assertFalse(_jacobian_ok(map_x, map_y, edge_margin=10))


class TestDewarpPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clean, cls.soal_a_truth = _draw_full_sheet()
        cls.curved = _sinusoidal_warp(cls.clean)
        cls.clean_bgr = cv2.cvtColor(cls.clean, cv2.COLOR_GRAY2BGR)
        cls.curved_bgr = cv2.cvtColor(cls.curved, cv2.COLOR_GRAY2BGR)

    def test_flat_clean_sheet_is_left_untouched(self):
        warped2, gray2, info = dewarp_page(self.clean_bgr, self.clean, ALL_FIELDS, CANVAS_W, CANVAS_H)
        self.assertFalse(info["applied"])
        self.assertTrue(np.array_equal(gray2, self.clean))
        self.assertTrue(np.array_equal(warped2, self.clean_bgr))

    def test_empty_paper_skips_dewarp(self):
        blank = np.full_like(self.clean, 255)
        blank_bgr = cv2.cvtColor(blank, cv2.COLOR_GRAY2BGR)
        warped2, gray2, info = dewarp_page(blank_bgr, blank, ALL_FIELDS, CANVAS_W, CANVAS_H)
        self.assertFalse(info["applied"])
        self.assertTrue(np.array_equal(gray2, blank))

    def test_recovers_sinusoidal_curvature(self):
        # Sebelum koreksi: kelengkungan mengacaukan kolom NAMA yg tinggi (26 baris) -- verifikasi
        # dulu memang rusak sebelum dewarp, spt pola test_shifted_sheet_unreadable_before_and_readable_after.
        before = _decode(self.curved, ALL_FIELDS["NAMA"], "NAMA")
        self.assertNotEqual(before["NAMA"].rstrip(), NAMA_MARK_TRIMMED)

        warped2, gray2, info = dewarp_page(self.curved_bgr, self.curved, ALL_FIELDS, CANVAS_W, CANVAS_H)
        self.assertTrue(info["applied"], info)
        self.assertGreater(info["contrast_after"], info["contrast_before"])

        after_nama = _decode(gray2, ALL_FIELDS["NAMA"], "NAMA")
        self.assertEqual(after_nama["NAMA"].rstrip(), NAMA_MARK_TRIMMED)

        after_soal = _decode(gray2, ALL_FIELDS["Soal-A"], "Soal-A")
        for item_name, truth in self.soal_a_truth.items():
            self.assertEqual(after_soal.get(item_name), truth, item_name)

    def test_contrast_gate_needs_real_improvement(self):
        # Peta identitas (tak ada koreksi nyata) tak boleh lolos gerbang kontras, bahkan kalau
        # gerbang cakupan/jacobian lain lolos -- dicek langsung lewat _global_grid_contrast, bukan
        # dewarp_page (yang fit TPS asli dari citra bersih hasilnya memang ~identitas juga).
        c = _global_grid_contrast(self.clean, ALL_FIELDS)
        self.assertGreater(c, 30.0)   # kertas bersih: kontras kisi harus tinggi, gerbang 3.0 terlalu kecil utk ini


if __name__ == "__main__":
    unittest.main()
