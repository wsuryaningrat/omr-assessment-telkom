"""Framework-agnostic LJK scanning service.

Tidak bergantung pada Streamlit — dipakai oleh app.py (Streamlit) sekarang dan
oleh API/worker (FastAPI) setelah migrasi. Logika pemindaian identik dengan
versi sebelumnya (dijaga oleh tests/regression).
"""
import json
import os
import re
from datetime import datetime, timezone, timedelta

import cv2

from core.alignment import detect_corners_and_crop
from core.decoder import decode_field
from core.evaluator import find_matching_kunci_sheet, grade_student_record
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


def scan_page(img_bgr, doc_name, template, fakultas_pilihan, nama_pengawas, k_cache, pengawas_info=None, with_overlay=False):
    """Runs the full OMR pipeline (alignment + decode + grading) on one page/photo
    and returns (student_record, preview). Shared by the batch scan and the
    per-row 'Ganti Foto' replacement flow so both stay perfectly in sync."""
    canvas_w = template.get("canvas", {}).get("width", 1700)
    canvas_h = template.get("canvas", {}).get("height", 2400)
    fields_dict = template.get("fields", {})
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

    decoded_all = {}
    soal_dict = {}
    for fname, fdef in fields_dict.items():
        fdef_copy = dict(fdef)
        if "field_name" not in fdef_copy:
            fdef_copy["field_name"] = fname
        field_data = decode_field(gray_warped, fdef_copy, thresh=0.28, margin=0.06)
        decoded_all.update(field_data)
        if "soal" in fname.lower() and "kode" not in fname.lower():
            soal_dict.update(field_data)

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
        dyn_total_soal = len(soal_keys) if len(soal_keys) > 0 else 75
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
