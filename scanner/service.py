"""Framework-agnostic LJK scanning service.

Tidak bergantung pada Streamlit — dipakai oleh app.py (Streamlit) sekarang dan
oleh API/worker (FastAPI) setelah migrasi. Logika pemindaian identik dengan
versi sebelumnya (dijaga oleh tests/regression).
"""
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta

import cv2
import numpy as np

from core.alignment import detect_corners_and_crop
from core.cnn_reader import read_missing_npm_digits
from core.decoder import decode_field
from core.detector import calculate_fill_ratio
from core.evaluator import MAX_SOAL, find_matching_kunci_sheet, grade_student_record
from core.field_registration import register_fields, snap_rows
from core.second_opinion import refine_identity
from core.utils import draw_reading_overlay

# Direktori root proyek (tempat templates/ berada).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Batas sisi terpanjang foto saat di-decode. Default None = resolusi asli (akurasi identik
# dengan versi awal). Set env SCAN_MAX_SIDE (mis. 3000) hanya setelah diuji pada foto asli:
# uji internal menunjukkan hasil baca bisa berubah saat gambar diperkecil.
SCAN_MAX_SIDE = int(os.environ["SCAN_MAX_SIDE"]) if os.environ.get("SCAN_MAX_SIDE") else None


# Fakultas -> program studi (sumber: https://telkomuniversity.ac.id/program-sarjana/)
FAKULTAS_PRODI = {
    "FTE - Fakultas Teknik Elektro": [
        "S1 Teknik Fisika", "S1 Teknik Telekomunikasi", "S1 Teknik Biomedis",
        "S1 Teknik Sistem Energi", "S1 Teknik Elektro", "S1 Teknik Komputer",
    ],
    "FRI - Fakultas Rekayasa Industri": [
        "S1 Sistem Informasi", "S1 Teknik Industri", "S1 Teknik Logistik", "S1 Manajemen Rekayasa",
    ],
    "FIF - Fakultas Informatika": [
        "S1 Teknologi Informasi", "S1 Rekayasa Perangkat Lunak", "S1 Informatika",
        "S1 PJJ Informatika", "S1 Sains Data",
    ],
    "FEB - Fakultas Ekonomi dan Bisnis": [
        "S1 Manajemen", "S1 Akuntansi", "S1 Manajemen Bisnis Rekreasi",
        "S1 Administrasi Bisnis", "S1 Bisnis Digital",
    ],
    "FKS - Fakultas Komunikasi dan Ilmu Sosial": [
        "S1 Ilmu Komunikasi", "S1 Hubungan Masyarakat", "S1 Penyiaran Konten Digital", "S1 Psikologi",
    ],
    "FIK - Fakultas Industri Kreatif": [
        "S1 Desain Komunikasi Visual", "S1 Desain Produk", "S1 Desain Interior",
        "S1 Kriya", "S1 Seni Rupa", "S1 Film",
    ],
    "FIT - Fakultas Ilmu Terapan": [
        "D3 Teknologi Telekomunikasi", "D3 Rekayasa Perangkat Lunak Aplikasi", "D3 Sistem Informasi",
        "D3 Sistem Informasi Akuntansi", "D3 Teknologi Komputer", "D3 Manajemen Pemasaran",
        "D3 Perhotelan", "S1 Terapan Teknologi Rekayasa Multimedia",
        "S1 Terapan Sistem Informasi Kota Cerdas",
    ],
}


def get_default_template_path():
    """Finds the default template path: checks templates/ folder first, then fallback to template-final.json."""
    base_dir = BASE_DIR
    templates_dir = os.path.join(base_dir, "templates")
    if os.path.isdir(templates_dir):
        json_files = sorted(
            [f for f in os.listdir(templates_dir) if f.endswith(".json") and not f.startswith(".")],
            reverse=True
        )
        if json_files:
            return os.path.join(templates_dir, json_files[0])
    
    for fallback in ["templates/omr_config.json", "template-final.json", "templates/template_v1.json"]:
        p = os.path.join(base_dir, fallback)
        if os.path.exists(p):
            return p
    return os.path.join(base_dir, "template-final.json")


def load_default_template():
    tpl_path = get_default_template_path()
    if os.path.exists(tpl_path):
        try:
            with open(tpl_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Gagal membaca {tpl_path}:", e)
    return None


def classify_scan_status(preview_item, validated):
    """Classifies a scanned sheet as Gagal (alignment failed, needs a new photo),
    Perlu Validasi (scanned fine, awaiting human confirmation), or OK (validated).
    Never derived from the grade."""
    status_str = (preview_item or {}).get("status", "") if isinstance(preview_item, dict) else ""
    if "DETECTED" not in status_str:
        return "Gagal"
    return "OK" if validated else "Perlu Validasi"


def is_valid_phone(value):
    digits = re.sub(r"[\s\-()+]", "", value or "")
    return digits.isdigit() and 9 <= len(digits) <= 15


_log = logging.getLogger("scanner.service")


def _limit_soal(fields):
    """Buang butir soal bernomor > MAX_SOAL dari blok jawaban (Soal-*); blok yang habis ikut dibuang.
    Efeknya: tidak dibaca, tidak digambar di preview, tidak masuk rekap/"Jawaban Terisi"."""
    out = {}
    for name, fdef in fields.items():
        low = name.lower()
        if "soal" in low and "kode" not in low:
            items = []
            for it in fdef.get("items", []):
                m = re.search(r"(\d+)$", str(it.get("name", "")))
                if m is None or int(m.group(1)) <= MAX_SOAL:
                    items.append(it)
            if not items:
                continue
            if len(items) != len(fdef["items"]):
                fdef = dict(fdef, items=items)
        out[name] = fdef
    return out


def _decode_fields(gray, fields_dict, only=None):
    """Baca semua blok (atau hanya yang namanya diawali salah satu `only`). Kembalikan (decoded_all, soal_dict)."""
    decoded_all, soal_dict = {}, {}
    for fname, fdef in fields_dict.items():
        if only and not fname.lower().startswith(only):
            continue
        fdef_copy = dict(fdef)
        if "field_name" not in fdef_copy:
            fdef_copy["field_name"] = fname
        field_data = decode_field(gray, fdef_copy, thresh=0.28, margin=0.06)
        decoded_all.update(field_data)
        if "soal" in fname.lower() and "kode" not in fname.lower():
            soal_dict.update(field_data)
    return decoded_all, soal_dict


def _identity_complete(decoded_all, fields_dict):
    """True bila NIM dan kode soal terbaca utuh (tiap kolom satu digit, tanpa kosong/'?')."""
    for name in ("NPM", "KODE SOAL"):
        field = fields_dict.get(name)
        if not field:
            continue
        val = decoded_all.get(name, "")
        if len(val) != len(field["items"]) or not val.isdigit():
            return False
    return True


_RESCUE_MIN_TOP, _RESCUE_MIN_GAP = 0.50, 0.12   # keyakinan minimum penyelamatan (silang asli: 0.54-0.76; noise: <= 0.38)


def _rescue_answers(gray, fields, decoded, soal_dict):
    """Baris jawaban yang terbaca KOSONG/GANDA dicoba sekali lagi dengan posisi kotak diselaraskan per baris
    (kertas fotokopi tidak rata: geser tiap baris berbeda). Hasil baru hanya dipakai bila silangnya jelas
    (skor tertinggi >= 0.50 dan unggul >= 0.12 dari kandidat kedua); baris yang sudah terbaca yakin TIDAK PERNAH
    diubah. Memodifikasi decoded & soal_dict di tempat; kembalikan jumlah baris yang terselamatkan."""
    snapped, _ = snap_rows(gray, fields, only=("soal",))
    again, _ = _decode_fields(gray, snapped, only=("soal",))
    paper_bg = float(np.percentile(gray, 92))
    if paper_bg < 150:
        paper_bg = 240.0
    items = {it["name"]: it for f in snapped.values() for it in f["items"]}
    n = 0
    for k, v in again.items():
        if decoded.get(k) not in ("BLANK", "MULTIPLE") or v in ("BLANK", "MULTIPLE", "?", None, ""):
            continue
        ratios = sorted((calculate_fill_ratio(gray, b["cx"], b["cy"], b.get("radius", 12), shape=b.get("shape", "square"),
                                              w=b.get("w"), h=b.get("h"), paper_bg=paper_bg, option_glyph=b.get("option"))
                         for b in items[k]["bubbles"]), reverse=True)
        if ratios[0] >= _RESCUE_MIN_TOP and ratios[0] - ratios[1] >= _RESCUE_MIN_GAP:
            decoded[k] = v
            soal_dict[k] = v
            n += 1
    return n


def _cnn_rescue_npm(gray, fields, decoded_all, doc_name):
    """Lengkapi digit NPM yang masih kosong/'?' setelah pembaca utama + registrasi, pakai CNN mini
    (core.cnn_reader) — dilatih pada lembar ujian asli, jauh lebih tahan geser posisi daripada rasio-gelap.
    Hanya mengisi kolom yang BELUM terbaca dan lolos gerbang keyakinan; digit yang sudah terbaca (dan kolom
    yang gerbangnya tak terlewati) tidak pernah disentuh. Kembalikan (npm_baru, {kolom: digit} yg diisi)."""
    field = fields.get("NPM")
    npm = decoded_all.get("NPM", "")
    if not field:
        return npm, {}
    n = len(field["items"])
    chars = list(npm.ljust(n))[:n]
    unresolved = [i for i, c in enumerate(chars) if c in (" ", "?")]
    if not unresolved:
        return npm, {}
    filled = read_missing_npm_digits(gray, field, unresolved)
    for col, digit in filled.items():
        chars[col] = digit
    return "".join(chars).rstrip(), filled


def _retry_with_registration(gray, fields_dict, decoded_all, soal_dict, doc_name):
    fields2, info = register_fields(gray, fields_dict)
    decoded2, soal2 = _decode_fields(gray, fields2)
    decoded2 = refine_identity(gray, fields2, decoded2, info)
    rescued = _rescue_answers(gray, fields2, decoded2, soal2)
    before_npm = decoded2.get("NPM")
    decoded2["NPM"], cnn_filled = _cnn_rescue_npm(gray, fields2, decoded2, doc_name)
    moved = {k: (v["dx"], v["dy"]) for k, v in info.items() if v.get("applied")}
    _log.info("koreksi posisi kotak %s: %s | NPM %r -> %r -> %r (cnn: %s) | KODE %r -> %r | jawaban terselamatkan: %d",
              doc_name, moved, decoded_all.get("NPM"), before_npm, decoded2["NPM"], cnn_filled,
              decoded_all.get("KODE SOAL"), decoded2.get("KODE SOAL"), rescued)
    return fields2, decoded2, soal2


def scan_page(img_bgr, doc_name, template, fakultas_pilihan, nama_pengawas, k_cache, pengawas_info=None, with_overlay=False):
    """Runs the full OMR pipeline (alignment + decode + grading) on one page/photo
    and returns (student_record, preview). Shared by the batch scan and the
    per-row 'Ganti Foto' replacement flow so both stay perfectly in sync."""
    canvas_w = template.get("canvas", {}).get("width", 1700)
    canvas_h = template.get("canvas", {}).get("height", 2400)
    fields_dict = _limit_soal(template.get("fields", {}))
    k_cache = {n: {q: a for q, a in d.items() if q <= MAX_SOAL} for n, d in (k_cache or {}).items()}
    aruco_dict = template.get("aruco_dict", "DICT_4X4_50")
    expected_ids = template.get("aruco_corner_ids")
    crop_m = "inner"

    warped, pts, method, c_ids, _, status, _ = detect_corners_and_crop(
        img_bgr, canvas_w=canvas_w, canvas_h=canvas_h, preferred_method="aruco",
        expected_ids=expected_ids, dict_name=aruco_dict, crop_mode=crop_m
    )
    gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    wib_tz = timezone(timedelta(hours=7))
    current_submit_time = datetime.now(wib_tz).strftime("%Y-%m-%d %H:%M")

    decoded_all, soal_dict = _decode_fields(gray_warped, fields_dict)
    if not _identity_complete(decoded_all, fields_dict):
        # NIM/kode tidak terbaca utuh -> biasanya isi lembar bergeser dari posisi template (fotokopi
        # menskalakan/menggeser isi 1-3 %, sampai setengah tinggi kotak). Coba lagi dengan posisi
        # kotak dikoreksi terhadap garis tercetak. Lembar yang sudah terbaca utuh TIDAK pernah lewat sini.
        fields_dict, decoded_all, soal_dict = _retry_with_registration(gray_warped, fields_dict, decoded_all, soal_dict, doc_name)

    student_record = {
        "Submit Date": current_submit_time,
        "Nama Pengawas": nama_pengawas.strip() if (nama_pengawas and nama_pengawas.strip()) else "-",
        "No HP Pengawas": (pengawas_info or {}).get("hp", "-") or "-",
        "Ruangan": (pengawas_info or {}).get("ruangan", "-") or "-",
        **({"Kelas": pengawas_info["kelas"]} if (pengawas_info or {}).get("kelas") else {}),
        "File": doc_name,
        "NPM": decoded_all.get("NPM", "-"),
        "Nama Mahasiswa": decoded_all.get("NAMA", "-"),
        "Fakultas": decoded_all.get("FAKULTAS", "-"),
        "Program Studi": (pengawas_info or {}).get("prodi", "-") or "-",
        "Fakultas (LJK)": decoded_all.get("FAKULTAS", "-"),
        "Kode Soal": decoded_all.get("KODE SOAL", decoded_all.get("Kode Soal", "-")),
    }

    kuis_keys = [k for k in decoded_all.keys() if (k.lower().startswith("q") and len(k) <= 5) or "kuis" in k.lower()]
    for k in sorted(kuis_keys):
        student_record[k] = decoded_all[k]

    if soal_dict:
        soal_keys = sorted(list(soal_dict.keys()))
    else:
        soal_keys = sorted([k for k in decoded_all.keys() if re.match(r"^soal_\d{2}$", k)])
    for k in soal_keys:
        student_record[k] = soal_dict.get(k, decoded_all.get(k, "BLANK"))

    detected_kode = student_record["Kode Soal"]
    matched_sh = find_matching_kunci_sheet(detected_kode, list(k_cache.keys())) if k_cache else None
    if matched_sh and matched_sh in k_cache and len(k_cache[matched_sh]) > 0:
        kunci_for_doc = k_cache[matched_sh]
        dyn_total_soal = len(kunci_for_doc)
        soal_terisi = sum(
            1 for q in kunci_for_doc.keys()
            if student_record.get(f"soal_{q:02d}", student_record.get(f"soal_{q}", "BLANK")) not in ["BLANK", "?", None, "", "NONE"]
        )
    elif len(k_cache) > 0 and len(list(k_cache.values())[0]) > 0:
        first_kunci = list(k_cache.values())[0]
        dyn_total_soal = len(first_kunci)
        soal_terisi = sum(
            1 for q in first_kunci.keys()
            if student_record.get(f"soal_{q:02d}", student_record.get(f"soal_{q}", "BLANK")) not in ["BLANK", "?", None, "", "NONE"]
        )
    else:
        dyn_total_soal = len(soal_keys) if len(soal_keys) > 0 else MAX_SOAL
        soal_terisi = sum(1 for k in soal_keys if student_record.get(k, "BLANK") not in ["BLANK", "?", None, "", "NONE"])

    student_record["Jawaban Terisi"] = f"{soal_terisi} / {dyn_total_soal}"

    if k_cache:
        grade_student_record(student_record, k_cache)
    else:
        student_record["Nilai"] = "-"
        student_record["Jumlah Benar"] = "-"
        student_record["Jumlah Salah"] = "-"
        student_record["Jumlah Kosong"] = "-"
        student_record["Kunci Terpakai"] = "-"

    # Tidak menyimpan citra (overlay/warped) — hanya status, supaya RAM per sesi kecil.
    # Preview dibuat on-demand lewat build_preview_on_demand().
    preview = {
        "name": doc_name,
        "status": status,
        "method": method
    }
    if with_overlay:
        # Hanya dibuat saat pengawas meminta preview; tidak pernah disimpan di session.
        preview["overlay"] = draw_reading_overlay(warped, fields_dict, gray_warped, thresh=0.28, margin=0.08)
    return student_record, preview


# Nama lama dipertahankan agar pemanggil yang ada tetap berjalan.
process_single_page = scan_page
