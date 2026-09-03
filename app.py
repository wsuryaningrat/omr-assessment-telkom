import streamlit as st
from streamlit_gsheets import GSheetsConnection
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass
import cv2
import numpy as np
import pandas as pd
import json
import base64
import os
import time
from PIL import Image
import streamlit.components.v1 as components
from core.alignment import (
    find_aruco_markers,
    find_regmarks,
    detect_corners_and_crop,
    perspective_warp,
    draw_regmarks_overlay,
    rotate_image
)
from core.detector import (
    detect_bubbles_in_roi,
    auto_detect_and_align_grid,
    remove_duplicate_detections,
    group_into_rows,
    group_into_columns,
    regularize_grid
)
from core.decoder import decode_field
from core.pdf_utils import extract_images_from_file
from core.utils import (
    draw_field_overlay,
    draw_all_fields_overlay,
    draw_reading_overlay,
    export_to_csv,
    export_to_json
)

st.set_page_config(page_title="Telkom University - OMR Assessment System", page_icon="🎓", layout="wide")

# Register custom interactive ROI editor component safely
_roi_editor = None
try:
    _roi_comp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "roi_editor")
    if os.path.exists(_roi_comp_dir):
        _roi_editor = components.declare_component("roi_editor", path=_roi_comp_dir)
except Exception as _e:
    _roi_editor = None

def get_telkom_logo_b64():
    logo_path = os.path.join(os.path.dirname(__file__), "assets", "telkom_logo.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("utf-8")
    return ""

def load_default_template():
    tpl_path = os.path.join(os.path.dirname(__file__), "template-final.json")
    if os.path.exists(tpl_path):
        try:
            with open(tpl_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("Gagal membaca template-final.json:", e)
    return None

def render_roi_editor(image_bgr, box, label="Area Scan", key=None):
    """
    Renders an interactive live canvas where the user can drag and resize the ROI box directly.
    """
    if _roi_editor is None:
        return None
    try:
        h, w = image_bgr.shape[:2]
        preview_w = 850
        preview_h = int(preview_w * (h / w))
        small_bgr = cv2.resize(image_bgr, (preview_w, preview_h))
        _, buffer = cv2.imencode(".jpg", small_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        img_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer).decode("utf-8")

        return _roi_editor(
            image_b64=img_b64,
            box=box,
            label=label,
            orig_w=w,
            orig_h=h,
            key=key
        )
    except Exception:
        return None

def cv_to_pil(img_bgr):
    if len(img_bgr.shape) == 2:
        return Image.fromarray(img_bgr)
    return Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))

# ------------------------------------------------------------------------------
# TELKOM UNIVERSITY DESIGN SYSTEM (CSS INJECTION)
# ------------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
}

/* Telkom University Red Accent Line at the top */
header[data-testid="stHeader"] {
    border-top: 4px solid #BA0C2F !important;
    background-color: #FFFFFF !important;
}

/* Force Clean Light Theme Throughout App */
.stApp {
    background-color: #F8FAFC !important;
    color: #0F172A !important;
}

/* Force Sidebar to Clean White Background with High-Contrast Solid Black Text */
section[data-testid="stSidebar"] {
    background-color: #FFFFFF !important;
    border-right: 1px solid #CBD5E1 !important;
}

section[data-testid="stSidebar"] * {
    color: #0F172A !important;
}

section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] div,
section[data-testid="stSidebar"] small,
section[data-testid="stSidebar"] caption,
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] h4 {
    color: #0F172A !important;
}

/* Sidebar Radio Buttons */
section[data-testid="stSidebar"] div[data-testid="stRadio"] label {
    color: #0F172A !important;
    font-weight: 600 !important;
}

section[data-testid="stSidebar"] div[data-testid="stRadio"] label span {
    color: #0F172A !important;
}

section[data-testid="stSidebar"] div[data-testid="stRadio"] label[data-checked="true"] span {
    color: #BA0C2F !important;
    font-weight: 700 !important;
}

section[data-testid="stSidebar"] div[data-testid="stRadio"] label[data-checked="true"] div:first-child {
    border-color: #BA0C2F !important;
    background-color: #BA0C2F !important;
}

/* Sidebar File Uploader text */
section[data-testid="stSidebar"] div[data-testid="stFileUploader"] * {
    color: #0F172A !important;
}

/* Telkom Header Banner Container */
.telkom-header-container {
    background: #FFFFFF;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 16px 24px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px 0 rgba(0, 0, 0, 0.03);
}
.telkom-header-brand {
    display: flex;
    align-items: center;
    gap: 16px;
}
.telkom-header-logo {
    height: 44px;
    width: auto;
    object-fit: contain;
}
.telkom-header-text {
    display: flex;
    flex-direction: column;
}
.telkom-header-unit {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #BA0C2F;
    text-transform: uppercase;
}
.telkom-header-title {
    font-size: 19px;
    font-weight: 800;
    color: #0F172A;
    letter-spacing: -0.015em;
}
.telkom-header-desc {
    font-size: 12px;
    color: #475569;
    margin-top: 2px;
}
.telkom-header-badge {
    display: inline-flex;
    align-items: center;
    padding: 6px 14px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    background-color: #FFF1F2;
    color: #BA0C2F;
    border: 1px solid #FECDD3;
}

/* Global Headings & Typography */
h1, h2, h3, h4, h5, h6 {
    color: #0F172A !important;
    font-weight: 700 !important;
}

p, span, label {
    color: #1E293B;
}

/* Primary Button Styling (Telkom Red) */
button[kind="primary"] {
    background-color: #BA0C2F !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05) !important;
    transition: all 0.2s ease !important;
}
button[kind="primary"]:hover {
    background-color: #980925 !important;
    box-shadow: 0 4px 8px -1px rgba(186, 12, 47, 0.3) !important;
}
button[kind="primary"]:active {
    background-color: #7B061D !important;
}

/* Secondary Button Styling */
button[kind="secondary"] {
    border-radius: 8px !important;
    border: 1px solid #CBD5E1 !important;
    font-weight: 500 !important;
    color: #1E293B !important;
    background-color: #FFFFFF !important;
    transition: all 0.2s ease !important;
}
button[kind="secondary"]:hover {
    border-color: #BA0C2F !important;
    color: #BA0C2F !important;
    background-color: #FFF1F2 !important;
}

/* Card Containers */
.telkom-card {
    background: #FFFFFF !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 12px !important;
    padding: 18px 24px !important;
    margin-bottom: 18px !important;
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.04) !important;
}

/* High Contrast for all Inputs, Textboxes, and Selectboxes */
input, textarea, select {
    background-color: #FFFFFF !important;
    color: #0F172A !important;
    border: 1.5px solid #94A3B8 !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
}

input:focus, textarea:focus, select:focus {
    border-color: #BA0C2F !important;
    outline: none !important;
    box-shadow: 0 0 0 2px rgba(186, 12, 47, 0.2) !important;
}

div[data-baseweb="input"] {
    background-color: #FFFFFF !important;
    border: 1.5px solid #94A3B8 !important;
    border-radius: 8px !important;
}
div[data-baseweb="input"] input {
    color: #0F172A !important;
    background-color: #FFFFFF !important;
    font-weight: 500 !important;
}

div[data-baseweb="select"] {
    background-color: #FFFFFF !important;
    border: 1.5px solid #94A3B8 !important;
    border-radius: 8px !important;
}
div[data-baseweb="select"] * {
    color: #0F172A !important;
    background-color: #FFFFFF !important;
    font-weight: 600 !important;
}

/* Dropdown popover menu - max 3 items visible, scrollable */
div[data-baseweb="popover"] ul,
div[data-baseweb="menu"] {
    max-height: 135px !important;
    overflow-y: auto !important;
    background-color: #FFFFFF !important;
    border: 1.5px solid #CBD5E1 !important;
    border-radius: 8px !important;
    box-shadow: 0 4px 14px rgba(0,0,0,0.12) !important;
}

div[data-baseweb="popover"] li,
div[data-baseweb="menu"] li {
    color: #0F172A !important;
    background-color: #FFFFFF !important;
    padding: 8px 14px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}

div[data-baseweb="popover"] li:hover,
div[data-baseweb="menu"] li:hover,
div[data-baseweb="menu"] li[aria-selected="true"] {
    background-color: #FFF1F2 !important;
    color: #BA0C2F !important;
    font-weight: 600 !important;
}

/* File Uploader Container & Dropzone: Background Putih/Cerah & Semua Teks Hitam */
div[data-testid="stFileUploader"],
section[data-testid="stFileUploadDropzone"],
div[data-testid="stFileUploadDropzone"],
div[data-testid="stFileDropzone"],
div[data-testid="stFileUploadDropzoneInstructions"] {
    background-color: #FFFFFF !important;
    background: #FFFFFF !important;
    border: 2px dashed #94A3B8 !important;
    border-radius: 10px !important;
}
div[data-testid="stFileUploader"]:hover,
section[data-testid="stFileUploadDropzone"]:hover {
    border-color: #0F172A !important;
    background-color: #F8FAFC !important;
    background: #F8FAFC !important;
}

/* Seluruh Teks di Kolom Unggah & Dropzone Hitam Pekat */
div[data-testid="stFileUploader"] *,
section[data-testid="stFileUploadDropzone"] * {
    color: #0F172A !important;
}
div[data-testid="stFileUploader"] small,
div[data-testid="stFileUploader"] span,
div[data-testid="stFileUploader"] p,
div[data-testid="stFileUploader"] label,
div[data-testid="stFileUploader"] div,
section[data-testid="stFileUploadDropzone"] span,
section[data-testid="stFileUploadDropzone"] small,
section[data-testid="stFileUploadDropzone"] p {
    color: #0F172A !important;
    font-weight: 600 !important;
}

/* Tombol Upload (Browse files) Warna Cerah dengan Tulisan Hitam */
section[data-testid="stFileUploadDropzone"] button,
div[data-testid="stFileUploader"] button,
button[data-testid="baseButton-secondary"] {
    background-color: #F1F5F9 !important;
    background: #F1F5F9 !important;
    color: #0F172A !important;
    border: 1.5px solid #64748B !important;
    border-radius: 6px !important;
    padding: 8px 18px !important;
    font-weight: 700 !important;
    font-size: 13px !important;
    cursor: pointer !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08) !important;
    transition: all 0.2s ease !important;
}
section[data-testid="stFileUploadDropzone"] button:hover,
div[data-testid="stFileUploader"] button:hover,
button[data-testid="baseButton-secondary"]:hover {
    background-color: #E2E8F0 !important;
    background: #E2E8F0 !important;
    border-color: #0F172A !important;
    color: #000000 !important;
}
section[data-testid="stFileUploadDropzone"] button *,
div[data-testid="stFileUploader"] button *,
button[data-testid="baseButton-secondary"] * {
    color: #0F172A !important;
    font-weight: 700 !important;
}
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------------
# TOP TELKOM BRAND HEADER
# ------------------------------------------------------------------------------
logo_b64 = get_telkom_logo_b64()
st.markdown(f"""
<div class="telkom-header-container">
    <div class="telkom-header-brand">
        <img src="{logo_b64}" alt="Telkom University" class="telkom-header-logo"/>
        <div class="telkom-header-text">
            <span class="telkom-header-unit">PUSAT MATEMATIKA &bull; PRE-TEST KEMAMPUAN DASAR</span>
            <span class="telkom-header-title">Sistem Evaluasi OMR LJK Presisi Tinggi</span>
        </div>
    </div>
    <div class="telkom-header-badge">
        <span>Tahun Akademik 2026/2027</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------------
# INITIALIZE SESSION STATE & AUTO-SELECT template-final.json
# ------------------------------------------------------------------------------
if "calibrated_fields" not in st.session_state:
    st.session_state["calibrated_fields"] = {}
if "template_metadata" not in st.session_state:
    st.session_state["template_metadata"] = {"width": 1700, "height": 2400}
if "chosen_bubble_shape" not in st.session_state:
    st.session_state["chosen_bubble_shape"] = "square"
if "manual_rotation" not in st.session_state:
    st.session_state["manual_rotation"] = 0
if "last_loaded_json_id" not in st.session_state:
    st.session_state["last_loaded_json_id"] = None
if "editing_field_name" not in st.session_state:
    st.session_state["editing_field_name"] = "NAMA"

# Auto-load template-final.json by default on dashboard, refreshing automatically if updated on disk
tpl_file_path = os.path.join(os.path.dirname(__file__), "template-final.json")
cur_tpl_mtime = os.path.getmtime(tpl_file_path) if os.path.exists(tpl_file_path) else 0

if "default_template_loaded" not in st.session_state or st.session_state.get("loaded_template_mtime") != cur_tpl_mtime:
    default_tpl = load_default_template()
    if default_tpl and "fields" in default_tpl:
        st.session_state["calibrated_fields"] = default_tpl["fields"]
        st.session_state["template_metadata"] = default_tpl.get("canvas", {"width": 1700, "height": 2400})
        st.session_state["chosen_bubble_shape"] = default_tpl.get("bubble_shape", "square")
        if default_tpl["fields"]:
            st.session_state["editing_field_name"] = list(default_tpl["fields"].keys())[0]
        st.session_state["default_template_loaded"] = True
        st.session_state["loaded_template_mtime"] = cur_tpl_mtime

# High-contrast, clean sidebar with ONLY logo
st.sidebar.markdown(f"""
<div style="display: flex; justify-content: center; align-items: center; padding: 6px 0 14px 0; margin-bottom: 12px; border-bottom: 1px solid #E2E8F0;">
    <img src="{logo_b64}" style="height: 48px; width: auto; object-fit: contain;"/>
</div>
""", unsafe_allow_html=True)

mode = st.sidebar.radio(
    "Pilih Portal / Tampilan:",
    [
        "📋 Portal Dosen Pengawas (Upload & Evaluasi LJK)",
        "⚙️ Kalibrasi Template LJK (Admin)",
        "📊 OMR Reader & Batch Evaluator Lengkap (Admin)"
    ],
    index=0,
    help="Dosen Pengawas: Pilih portal pertama untuk mengunggah dan menilai LJK mahasiswa dengan sangat mudah."
)

# Quick badge for default template in sidebar
st.sidebar.markdown("""
<div style="background-color: #FFF1F2; border: 1px solid #FECDD3; border-radius: 8px; padding: 10px 12px; margin-top: 14px; margin-bottom: 12px;">
    <div style="font-size: 11px; font-weight: 700; color: #BA0C2F; text-transform: uppercase;">⭐ Template Default Aktif</div>
    <div style="font-size: 12px; font-weight: 700; color: #0F172A; margin-top: 2px;">template-final.json</div>
    <div style="font-size: 11px; color: #334155; margin-top: 2px;">11 Section &bull; 75 Soal Ujian Resmi</div>
</div>
""", unsafe_allow_html=True)

if st.sidebar.button("🔄 Muat Ulang template-final.json", use_container_width=True, help="Kembalikan konfigurasi field ke template-final.json bawaan"):
    default_tpl = load_default_template()
    if default_tpl and "fields" in default_tpl:
        st.session_state["calibrated_fields"] = default_tpl["fields"]
        st.session_state["template_metadata"] = default_tpl.get("canvas", {"width": 1700, "height": 2400})
        st.session_state["chosen_bubble_shape"] = default_tpl.get("bubble_shape", "square")
        st.session_state["editing_field_name"] = list(default_tpl["fields"].keys())[0]
        for k in list(st.session_state.keys()):
            if k.startswith("active_box_") or k.startswith("roi_editor_"):
                del st.session_state[k]
        st.toast("✅ template-final.json berhasil dimuat ulang!", icon="⭐")
        st.rerun()

# ==============================================================================
# MODE UTAMA: PORTAL DOSEN PENGAWAS (UPLOAD CEPAT & SUPER SIMPLE)
# ==============================================================================
if mode == "📋 Portal Dosen Pengawas (Upload & Evaluasi LJK)":
    st.markdown("""
    <div class="telkom-card" style="border-left: 5px solid #BA0C2F; margin-bottom: 18px; padding: 14px 20px;">
        <div style="font-size: 11px; font-weight: 700; color: #BA0C2F; text-transform: uppercase; letter-spacing: 0.08em;">PORTAL DOSEN PENGAWAS • EVALUASI LJK MAHASISWA</div>
        <h2 style="font-size: 20px; font-weight: 800; color: #0F172A; margin: 2px 0 4px 0;">📋 Unggah & Penilaian Lembar Jawaban (LJK) Peserta</h2>
        <p style="font-size: 12px; color: #334155; margin: 0;">Pilih fakultas dan unggah berkas LJK peserta untuk penilaian instan berbasis sistem visi komputer.</p>
    </div>
    """, unsafe_allow_html=True)

    # 1. Pilihan Fakultas & Nama Pengawas / Kode Dosen (Opsional)
    col_fak, col_dos = st.columns([1.3, 1.0])
    with col_fak:
        fakultas_pilihan = st.selectbox(
            "🏛️ Pilih Fakultas Mahasiswa Peserta:",
            options=[
                "FIF - Fakultas Informatika",
                "FRI - Fakultas Rekayasa Industri",
                "FTE - Fakultas Teknik Elektro",
                "FEB - Fakultas Ekonomi dan Bisnis",
                "FKB - Fakultas Komunikasi dan Bisnis",
                "FIK - Fakultas Industri Kreatif",
                "FIT - Fakultas Ilmu Terapan",
                "Semua Fakultas / Gabungan"
            ],
            index=0,
            help="Pilih fakultas mahasiswa yang sedang Anda awasi."
        )
    with col_dos:
        nama_pengawas = st.text_input(
            "👤 Nama Pengawas / Kode Dosen (Opsional):",
            placeholder="Contoh: Dr. Budi / KODE123",
            help="Opsional: untuk dicatat pada laporan rekap penilaian peserta."
        )

    # Google Sheets URL permanen (tanpa isian di antarmuka UI)
    TARGET_GSHEET_URL = "https://docs.google.com/spreadsheets/d/1vRpXz-w55XtX33WAx6b677yQXoM3oZ8jcQ_26m1XEYo/edit?usp=sharing"

    # 2. Layout Side-by-Side: Tombol Unggah di Kiri & Tombol Upload LJK di Kanan
    col_upload, col_action = st.columns([1.2, 1.0], gap="large")

    with col_upload:
        uploaded_files_dosen = st.file_uploader(
            "Upload Lembar Jawaban (PDF Multi-Halaman / JPG / PNG / HEIC iPhone):",
            type=["pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"],
            accept_multiple_files=True,
            help="Dukung berkas PDF multi-halaman, foto iPhone (HEIC/HEIF), maupun foto kamera/scan (JPG/PNG) sekaligus."
        )

        sample_filled_path = os.path.join(os.path.dirname(__file__), "filled_LJK.xlsx.pdf")
        if not uploaded_files_dosen and os.path.exists(sample_filled_path):
            if st.button("📄 Gunakan Berkas Contoh (filled_LJK.xlsx.pdf)", use_container_width=True):
                with open(sample_filled_path, "rb") as f:
                    pdf_bytes = f.read()
                    setattr(pdf_bytes, "name", "filled_LJK.xlsx.pdf")
                    st.session_state["dosen_test_files"] = [pdf_bytes]
                st.rerun()

        if not uploaded_files_dosen and st.session_state.get("dosen_test_files"):
            uploaded_files_dosen = st.session_state["dosen_test_files"]

    with col_action:
        if uploaded_files_dosen:
            total_berkas = len(uploaded_files_dosen)
            daftar_nama = [getattr(f, "name", f"Berkas_{i+1}") for i, f in enumerate(uploaded_files_dosen)]

            st.markdown(f"""
            <div style="background-color: #ECFDF5; border: 1.5px solid #10B981; border-radius: 8px; padding: 10px 14px; margin-bottom: 10px;">
                <div style="font-size: 14px; font-weight: 800; color: #065F46;">
                    ✅ {total_berkas} Berkas LJK Berhasil Terupload!
                </div>
                <div style="font-size: 11px; color: #047857; margin-top: 2px;">
                    Fakultas: <b>{fakultas_pilihan.split(' - ')[0]}</b> {f'&bull; Pengawas: <b>{nama_pengawas}</b>' if nama_pengawas.strip() else ''} &bull; Siap dievaluasi.
                </div>
            </div>
            """, unsafe_allow_html=True)

            # Scrollable box: shows ~3 items at a time, scrollable for the rest
            file_items_html = "".join([f"<div style='padding: 3px 0; border-bottom: 1px solid #F1F5F9; font-size: 12px;'>📄 {fn}</div>" for fn in daftar_nama])
            st.markdown(f"""
            <div style="font-size: 11px; font-weight: 600; color: #475569; margin-bottom: 4px;">Berkas Terunggah (Scroll jika &gt; 3 berkas):</div>
            <div style="max-height: 85px; overflow-y: auto; border: 1.5px solid #CBD5E1; border-radius: 6px; padding: 6px 10px; background: #FFFFFF; color: #0F172A; margin-bottom: 12px;">
                {file_items_html}
            </div>
            """, unsafe_allow_html=True)

            do_periksa = st.button("🚀 Upload LJK", type="primary", use_container_width=True)
        else:
            st.markdown("""
            <div style="background-color: #F8FAFC; border: 1.5px dashed #CBD5E1; border-radius: 8px; padding: 22px 14px; text-align: center; color: #64748B; font-size: 12px;">
                📁 <b>Belum ada berkas terunggah.</b><br>
                Silakan upload berkas LJK di sebelah kiri untuk mengaktifkan tombol pemeriksaan.
            </div>
            """, unsafe_allow_html=True)
            do_periksa = False

    if uploaded_files_dosen and do_periksa:
            template = load_default_template()
            if not template:
                st.error("Template resmi 'template-final.json' tidak ditemukan.")
                st.stop()

            canvas_w = template.get("canvas", {}).get("width", 1700)
            canvas_h = template.get("canvas", {}).get("height", 2400)
            fields_dict = template.get("fields", {})
            align_method = template.get("alignment_method", "aruco")
            aruco_dict = template.get("aruco_dict", "DICT_4X4_50")
            expected_ids = template.get("aruco_corner_ids")
            crop_m = template.get("crop_mode", "inner")

            all_pages_to_process = []
            with st.spinner("Mengekstrak seluruh halaman dokumen..."):
                for uf in uploaded_files_dosen:
                    try:
                        pages = extract_images_from_file(uf, target_dpi=200)
                        all_pages_to_process.extend(pages)
                    except Exception as e:
                        st.error(f"Error memproses berkas {getattr(uf, 'name', 'LJK')}: {str(e)}")

            st.info(f"Memeriksa **{len(all_pages_to_process)} lembar jawaban** dari {len(uploaded_files_dosen)} berkas terupload...")
            prog = st.progress(0)
            dosen_results = []
            dosen_previews = []

            for idx, (doc_name, img_bgr) in enumerate(all_pages_to_process):
                warped, pts, method, _, _, status = detect_corners_and_crop(
                    img_bgr,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    preferred_method=align_method,
                    expected_ids=expected_ids,
                    dict_name=aruco_dict,
                    crop_mode=crop_m
                )

                gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
                student_record = {
                    "File": doc_name,
                    "Fakultas (Pengawas)": fakultas_pilihan.split(" - ")[0],
                    "Pengawas / Dosen": nama_pengawas.strip() if nama_pengawas.strip() else "-",
                    "Status LJK": "Valid" if "DETECTED" in status else "Periksa Manual"
                }

                decoded_all = {}
                for fname, fdef in fields_dict.items():
                    fdef_copy = dict(fdef)
                    if "field_name" not in fdef_copy:
                        fdef_copy["field_name"] = fname
                    field_data = decode_field(gray_warped, fdef_copy, thresh=0.28, margin=0.08)
                    decoded_all.update(field_data)

                # Student Identity
                student_record["NPM"] = decoded_all.get("NPM", "-")
                student_record["Nama Mahasiswa"] = decoded_all.get("NAMA", "-")
                student_record["Fakultas (LJK)"] = decoded_all.get("FAKULTAS", "-")

                # Kuisioner items (e.g. q01 .. q15)
                kuis_keys = [k for k in decoded_all.keys() if (k.lower().startswith("q") and len(k) <= 5) or "kuis" in k.lower()]
                for k in sorted(kuis_keys):
                    student_record[k] = decoded_all[k]

                # Question statistics (75 questions)
                soal_keys = [k for k in decoded_all.keys() if k.startswith("soal_")]
                soal_terisi = sum(1 for k in soal_keys if decoded_all[k] not in ["BLANK", "?", None, ""])
                total_soal = len(soal_keys) if len(soal_keys) > 0 else 75
                student_record["Jawaban Terisi"] = f"{soal_terisi} / {total_soal}"
                student_record["Persentase Terisi"] = f"{(soal_terisi / total_soal * 100):.1f}%"

                # Append all questions
                for k in sorted(soal_keys):
                    student_record[k] = decoded_all[k]

                overlay_img = draw_reading_overlay(warped, fields_dict, gray_warped, thresh=0.28)
                dosen_previews.append((doc_name, overlay_img))
                dosen_results.append(student_record)
                prog.progress((idx + 1) / len(all_pages_to_process))

            st.session_state["dosen_results"] = dosen_results
            st.session_state["dosen_previews"] = dosen_previews

            # SINKRONISASI OTOMATIS KE GOOGLE SHEETS (PERMANEN)
            try:
                with st.spinner("Mentransfer data hasil konversi ke Google Sheet..."):
                    conn = st.connection("gsheets", type=GSheetsConnection)
                    df_to_sync = pd.DataFrame(dosen_results)
                    try:
                        existing_sheet_df = conn.read(spreadsheet=TARGET_GSHEET_URL, ttl=0).dropna(how="all")
                        if not existing_sheet_df.empty and "NPM" in existing_sheet_df.columns:
                            combined_sheet_df = pd.concat([existing_sheet_df, df_to_sync], ignore_index=True)
                            conn.update(spreadsheet=TARGET_GSHEET_URL, data=combined_sheet_df)
                        else:
                            conn.update(spreadsheet=TARGET_GSHEET_URL, data=df_to_sync)
                    except Exception:
                        conn.update(spreadsheet=TARGET_GSHEET_URL, data=df_to_sync)
                st.toast("✅ Data LJK berhasil ditransfer ke Google Sheet!", icon="📊")
                st.session_state["last_gsheet_status"] = ("success", f"Data berhasil disinkronkan ke Google Sheet ({len(dosen_results)} Peserta).")
            except Exception as e:
                err_msg = str(e)
                st.session_state["last_gsheet_status"] = ("error", f"Gagal transfer Google Sheet: {err_msg}")
                if "Public Spreadsheet cannot be written to" in err_msg:
                    st.error(
                        "⚠️ **Kredensial Service Account Belum Terpasang di Streamlit Cloud.**\n\n"
                        "Untuk mengaktifkan sinkronisasi otomatis ke Google Sheet di Cloud:\n"
                        "1. Di pojok kanan bawah, klik **Manage app** (atau menu **Settings** di share.streamlit.io).\n"
                        "2. Pilih tab **Secrets**.\n"
                        "3. Salin dan tempelkan konfigurasi `[connections.gsheets]` lengkap, lalu klik **Save**."
                    )
                else:
                    st.warning(f"⚠️ Gagal sinkronisasi Google Sheet: {err_msg}")

    # Show results if available
    if "dosen_results" in st.session_state and st.session_state["dosen_results"]:
        results = st.session_state["dosen_results"]
        st.markdown("---")
        st.markdown(f"### 📊 Rekap Hasil Penilaian ({len(results)} Mahasiswa)")

        if st.session_state.get("last_gsheet_status"):
            st_type, st_text = st.session_state["last_gsheet_status"]
            if st_type == "success":
                st.success(f"✅ {st_text}")
            else:
                st.warning(f"⚠️ {st_text}")

        df_full = pd.DataFrame(results)
        primary_cols = ["NPM", "Nama Mahasiswa", "Fakultas (LJK)", "Pengawas / Dosen", "Jawaban Terisi", "Persentase Terisi", "Status LJK"]
        display_cols = [c for c in primary_cols if c in df_full.columns]
        st.dataframe(df_full[display_cols], use_container_width=True, hide_index=True)

        col_dl1, col_dl2 = st.columns([1.2, 1.2])
        with col_dl1:
            csv_bytes = df_full.to_csv(index=False).encode("utf-8")
            clean_fak = fakultas_pilihan.split(" - ")[0].replace(" ", "_").replace("/", "_")
            st.download_button(
                "📥 Unduh Rekap Hasil Penilaian Lengkap (Excel / CSV)",
                data=csv_bytes,
                file_name=f"Rekap_LJK_{clean_fak}_{time.strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
                type="primary",
                use_container_width=True
            )

        with col_dl2:
            if st.button("🔄 Sinkronkan Ulang ke Google Sheet", use_container_width=True):
                try:
                    with st.spinner("Mentransfer data ke Google Sheet..."):
                        conn = st.connection("gsheets", type=GSheetsConnection)
                        conn.update(spreadsheet=TARGET_GSHEET_URL, data=df_full)
                    st.success("✅ Data berhasil disinkronkan ke Google Sheet!")
                    st.session_state["last_gsheet_status"] = ("success", f"Data berhasil disinkronkan ke Google Sheet ({len(df_full)} Peserta).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal transfer: {str(e)}")

        # Dashboard / Pratinjau Google Sheets
        with st.expander("📊 Pratinjau Langsung Data Google Sheets", expanded=False):
            try:
                conn = st.connection("gsheets", type=GSheetsConnection)
                df_sheets = conn.read(spreadsheet=TARGET_GSHEET_URL, ttl=60)
                st.dataframe(df_sheets, use_container_width=True)
            except Exception as e:
                st.info(f"Belum dapat membaca data Google Sheet: {str(e)}.")

        with st.expander("🔍 Pratinjau Visual Lembar Mahasiswa (Klik untuk Memeriksa Arsiran)", expanded=False):
            if st.session_state.get("dosen_previews"):
                sel_doc = st.selectbox("Pilih Lembar Mahasiswa:", [p[0] for p in st.session_state["dosen_previews"]])
                for p in st.session_state["dosen_previews"]:
                    if p[0] == sel_doc:
                        st.image(cv_to_pil(p[1]), use_container_width=True, caption=f"Hasil Pindai Visual: {sel_doc}")


# ==============================================================================
# MODE ADMIN 1: KALIBRASI TEMPLATE LJK (LIVE AREA EDITOR)
# ==============================================================================
elif mode == "⚙️ Kalibrasi Template LJK (Admin)":
    # --------------------------------------------------------------------------
    # SIDEBAR: MINIMALIST UPLOAD ONLY
    # --------------------------------------------------------------------------
    st.sidebar.markdown("---")
    uploaded_file = st.sidebar.file_uploader("📄 Upload LJK Template (PDF / Gambar / HEIC):", type=["pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"])
    uploaded_json_side = st.sidebar.file_uploader(
        "📂 Muat template.json Lain (Opsional):",
        type=["json"],
        key="mode1_json_side",
        help="Muat file template.json yang pernah disimpan sebelumnya."
    )

    if uploaded_json_side is not None:
        file_signature = f"{uploaded_json_side.name}_{uploaded_json_side.size}"
        if st.session_state.get("last_loaded_json_id") != file_signature:
            try:
                loaded_tpl = json.load(uploaded_json_side)
                if "fields" in loaded_tpl:
                    st.session_state["calibrated_fields"] = loaded_tpl["fields"]
                    if loaded_tpl["fields"]:
                        st.session_state["editing_field_name"] = list(loaded_tpl["fields"].keys())[0]
                if "bubble_shape" in loaded_tpl:
                    st.session_state["chosen_bubble_shape"] = loaded_tpl["bubble_shape"]
                if "canvas" in loaded_tpl:
                    st.session_state["template_metadata"] = loaded_tpl["canvas"]

                for k in list(st.session_state.keys()):
                    if k.startswith("active_box_") or k.startswith("roi_editor_"):
                        del st.session_state[k]

                st.session_state["last_loaded_json_id"] = file_signature
                st.toast(f"✅ Template JSON '{uploaded_json_side.name}' berhasil dimuat!", icon="📂")
                st.rerun()
            except Exception as e:
                st.error(f"Gagal memuat template JSON: {str(e)}")

    # Internal automated defaults
    pref_method = "auto"
    chosen_dict = "DICT_4X4_50"
    c_mode = "inner"

    # --------------------------------------------------------------------------
    # HEADER & BENTUK BUBBLE (COMPACT)
    # --------------------------------------------------------------------------
    col_hdr1, col_hdr2 = st.columns([3, 2])
    with col_hdr1:
        st.subheader("🎯 Mode 1: Kalibrasi LJK & Editor Area Scan")
        st.caption("Template aktif: **template-final.json** (11 Section)")
    with col_hdr2:
        shape_choice = st.radio(
            "Bentuk Bubble:",
            options=["⏹️ Kotak (Checkbox / Silang)", "🟢 Bulatan (Lingkaran / OMR)"],
            index=0 if st.session_state["chosen_bubble_shape"] == "square" else 1,
            horizontal=True
        )
        chosen_shape = "square" if "Kotak" in shape_choice else "circle"
        st.session_state["chosen_bubble_shape"] = chosen_shape

    # Quick sample PDF loader if user hasn't uploaded a file
    sample_ljk_path = os.path.join(os.path.dirname(__file__), "LJK.pdf")
    has_sample = os.path.exists(sample_ljk_path)

    if not uploaded_file:
        col_inf1, col_inf2 = st.columns([3, 1])
        with col_inf1:
            st.info("👋 Silakan upload file LJK template (PDF, JPG, atau PNG) di sidebar kiri untuk kalibrasi visual, atau gunakan LJK contoh resmi.")
        with col_inf2:
            if has_sample and st.button("📄 Muat LJK.pdf Contoh", type="primary", use_container_width=True):
                st.session_state["use_sample_ljk"] = True
                st.rerun()

        if st.session_state.get("use_sample_ljk") and has_sample:
            with open(sample_ljk_path, "rb") as f:
                uploaded_file = f.read()
                setattr(uploaded_file, "name", "LJK.pdf")
        else:
            if st.session_state["calibrated_fields"]:
                st.markdown("#### 📦 Rincian 11 Field dari template-final.json (Default Terpilih):")
                f_summary = []
                for fn, fd in st.session_state["calibrated_fields"].items():
                    tot_b = sum(len(it["bubbles"]) for it in fd.get("items", []))
                    f_summary.append({
                        "Nama Section": fn,
                        "Tipe": fd.get("field_type", "-"),
                        "Orientasi": fd.get("orientation", "-"),
                        "Jumlah Kolom/Soal": fd.get("item_count", len(fd.get("items", []))),
                        "Total Kotak": tot_b,
                        "Koordinat ROI [X, Y, W, H]": str(fd.get("roi", []))
                    })
                st.dataframe(pd.DataFrame(f_summary), use_container_width=True, hide_index=True)
            st.stop()

    try:
        extracted_pages = extract_images_from_file(uploaded_file, target_dpi=200)
    except Exception as e:
        st.error(f"Gagal memuat dokumen: {str(e)}")
        st.stop()

    if len(extracted_pages) > 1:
        page_names = [p[0] for p in extracted_pages]
        selected_page_name = st.selectbox("📄 Pilih Halaman PDF:", page_names)
        img_bgr = next(p[1] for p in extracted_pages if p[0] == selected_page_name)
    else:
        img_bgr = extracted_pages[0][1]

    # Quick Rotation Controls (Inline)
    col_r1, col_r2, col_r3, col_r4 = st.columns([4, 1, 1, 1])
    with col_r2:
        if st.button("🔄 Putar 90°"):
            st.session_state["manual_rotation"] = (st.session_state["manual_rotation"] + 90) % 360
            st.rerun()
    with col_r3:
        if st.button("🔄 Putar 180°"):
            st.session_state["manual_rotation"] = (st.session_state["manual_rotation"] + 180) % 360
            st.rerun()
    with col_r4:
        if st.button("🔄 Reset"):
            st.session_state["manual_rotation"] = 0
            st.rerun()

    if st.session_state["manual_rotation"] != 0:
        img_bgr = rotate_image(img_bgr, st.session_state["manual_rotation"])

    canvas_w = st.session_state["template_metadata"]["width"]
    canvas_h = st.session_state["template_metadata"]["height"]

    # Fast ArUco Alignment & Inner Rectangle Crop (<0.05s)
    t_start = time.time()
    warped_img, ordered_pts, method_used, corner_ids, detected_dict, status = detect_corners_and_crop(
        img_bgr,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        preferred_method=pref_method,
        dict_name=chosen_dict,
        crop_mode=c_mode
    )
    t_elapsed = time.time() - t_start

    st.session_state["warped_img"] = warped_img
    st.session_state["corner_pts"] = ordered_pts
    st.session_state["corner_ids"] = corner_ids
    st.session_state["detected_dict"] = detected_dict
    st.session_state["method_used"] = method_used
    st.session_state["crop_mode"] = c_mode

    # Compact Collapsible Preview for ArUco Corner Status
    with st.expander(f"📐 Penyelarasan Sudut Otomatis: {status} ({t_elapsed:.3f}s) — Klik untuk intip gambar crop", expanded=False):
        c_crop1, c_crop2 = st.columns(2)
        with c_crop1:
            regmarks_overlay = draw_regmarks_overlay(img_bgr, ordered_pts, method=method_used, corner_ids=corner_ids, status=status, crop_mode=c_mode)
            st.image(cv_to_pil(regmarks_overlay), caption="Posisi 4 Pojok Sudut", use_container_width=True)
        with c_crop2:
            st.image(cv_to_pil(warped_img), caption=f"Hasil Crop Bersih ({canvas_w}×{canvas_h} px)", use_container_width=True)

    # --------------------------------------------------------------------------
    # SECTION SELECTION / EDIT / CUSTOM CREATION (BUG-FREE & RELIABLE)
    # --------------------------------------------------------------------------
    saved_names = list(st.session_state["calibrated_fields"].keys())

    # Ensure there is always a valid active section
    if not saved_names:
        initial_name = "Soal_1_25"
        st.session_state["calibrated_fields"][initial_name] = {
            "field_name": initial_name,
            "field_type": "multiple_choice",
            "orientation": "Horizontal",
            "roi": [150, 1000, 420, 950],
            "cols": 4,
            "rows": 25,
            "item_count": 25,
            "options_per_item": 4,
            "cells": [],
            "items": []
        }
        st.session_state["editing_field_name"] = initial_name
        saved_names = [initial_name]

    if "editing_field_name" not in st.session_state or st.session_state["editing_field_name"] not in st.session_state["calibrated_fields"]:
        st.session_state["editing_field_name"] = saved_names[0]

    current_editing = st.session_state["editing_field_name"]
    cur_fdef = st.session_state["calibrated_fields"][current_editing]

    col_sec1, col_sec2, col_sec3 = st.columns([3, 2, 2])
    with col_sec1:
        if len(saved_names) > 1:
            cur_idx = saved_names.index(current_editing)
            chosen_to_edit = st.selectbox(
                "📂 Pilih Section untuk Diedit:",
                saved_names,
                index=cur_idx,
                key=f"sec_dropdown_{current_editing}"
            )
            if chosen_to_edit != current_editing:
                st.session_state["editing_field_name"] = chosen_to_edit
                if f"roi_editor_{chosen_to_edit}" in st.session_state:
                    del st.session_state[f"roi_editor_{chosen_to_edit}"]
                st.rerun()

        field_name = st.text_input("Nama Section:", value=current_editing, key=f"inp_name_{current_editing}")
        if field_name != current_editing and field_name.strip():
            new_key = field_name.strip()
            reordered = {}
            for k, v in st.session_state["calibrated_fields"].items():
                if k == current_editing:
                    v_renamed = dict(v)
                    v_renamed["field_name"] = new_key
                    reordered[new_key] = v_renamed
                else:
                    reordered[k] = v
            st.session_state["calibrated_fields"] = reordered
            if f"active_box_{current_editing}" in st.session_state:
                st.session_state[f"active_box_{new_key}"] = st.session_state.pop(f"active_box_{current_editing}")
            if f"roi_editor_{current_editing}" in st.session_state:
                del st.session_state[f"roi_editor_{current_editing}"]
            st.session_state["editing_field_name"] = new_key
            st.rerun()

    with col_sec2:
        type_opts = ["multiple_choice", "text", "number", "choice"]
        saved_t = cur_fdef.get("field_type", "multiple_choice")
        field_type = st.selectbox("Tipe Data:", type_opts, index=type_opts.index(saved_t) if saved_t in type_opts else 0, key=f"inp_type_{current_editing}")

    with col_sec3:
        st.write("")
        st.write("")
        if st.button("➕ Section Baru", type="secondary", use_container_width=True):
            new_idx = len(st.session_state["calibrated_fields"]) + 1
            new_name = f"Section_{new_idx}"
            while new_name in st.session_state["calibrated_fields"]:
                new_idx += 1
                new_name = f"Section_{new_idx}"

            # Smart offset positioning next to previous field
            lx, ly, lw, lh = cur_fdef.get("roi", [150, 150, 420, 500])
            nx = lx + lw + 40 if (lx + lw * 2 + 40) <= canvas_w else 150
            ny = ly if (lx + lw * 2 + 40) <= canvas_w else min(canvas_h - lh, ly + lh + 40)
            new_box = [nx, ny, lw, lh]

            # Immediately register new section
            st.session_state["calibrated_fields"][new_name] = {
                "field_name": new_name,
                "field_type": "multiple_choice",
                "orientation": "Horizontal",
                "roi": new_box,
                "cols": 4,
                "rows": 10,
                "item_count": 10,
                "options_per_item": 4,
                "cells": [],
                "items": []
            }
            st.session_state["editing_field_name"] = new_name
            st.session_state[f"active_box_{new_name}"] = new_box
            if f"roi_editor_{new_name}" in st.session_state:
                del st.session_state[f"roi_editor_{new_name}"]
            st.toast(f"✨ Section baru '{new_name}' siap diatur!", icon="➕")
            st.rerun()

    # Active bounding box
    box_state_key = f"active_box_{field_name}"
    comp_state_key = f"roi_editor_{field_name}"

    if comp_state_key in st.session_state and st.session_state[comp_state_key] is not None:
        comp_val = st.session_state[comp_state_key]
        if isinstance(comp_val, dict) and "x" in comp_val:
            st.session_state[box_state_key] = [
                int(comp_val["x"]), int(comp_val["y"]), int(comp_val["w"]), int(comp_val["h"])
            ]

    if box_state_key not in st.session_state:
        st.session_state[box_state_key] = list(cur_fdef.get("roi", [150, 150, 420, 500]))

    current_box = st.session_state[box_state_key]
    roi_x, roi_y, roi_w, roi_h = current_box

    # Dynamic auto-detection of rows and columns directly from image
    gray_warped = cv2.cvtColor(warped_img, cv2.COLOR_BGR2GRAY) if len(warped_img.shape) == 3 else warped_img
    roi_sub = gray_warped[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]

    auto_cols, auto_rows, auto_cells = auto_detect_and_align_grid(
        roi_sub, rx=roi_x, ry=roi_y, target_shape=chosen_shape,
        fallback_cols=cur_fdef.get("cols", 4), fallback_rows=cur_fdef.get("rows", 10)
    )

    # ==========================================================================
    # ULTRA-STREAMLINED SIDE-BY-SIDE LAYOUT (MINIMAL SCROLL)
    # Left: Interactive Canvas (Direct Drag & Resize)
    # Right: Label Mapping + Zoomed Preview + Action Buttons (All-in-One)
    # ==========================================================================
    col_canvas, col_settings = st.columns([1, 1], gap="medium")

    with col_settings:
        st.markdown(f"##### ⚙️ Pemetaan Grid & Label: **{field_name}**")

        c_dim1, c_dim2 = st.columns([3, 2])
        with c_dim1:
            auto_dim_toggle = st.toggle("🤖 Deteksi Baris & Kolom Otomatis dari Gambar", value=True,
                                        key=f"auto_dim_{field_name}",
                                        help="Membaca dan menghitung jumlah kolom dan baris kotak otomatis dari gambar tanpa perlu input manual.")
        with c_dim2:
            saved_orient = cur_fdef.get("orientation", "Horizontal")
            orientation = st.radio("Orientasi:", ["Horizontal", "Vertical"],
                                   index=0 if saved_orient == "Horizontal" else 1,
                                   key=f"orient_{field_name}",
                                   horizontal=True)

        if auto_dim_toggle:
            num_cols = auto_cols
            num_rows = auto_rows
            st.success(f"⚡ Terdeteksi dari Pola Gambar: **{num_cols} Kolom × {num_rows} Baris**")
        else:
            c_inp1, c_inp2 = st.columns(2)
            with c_inp1:
                num_cols = st.number_input("Jumlah Kolom:", min_value=1, max_value=40,
                                           value=cur_fdef.get("cols", auto_cols),
                                           key=f"cols_{field_name}")
            with c_inp2:
                num_rows = st.number_input("Jumlah Baris:", min_value=1, max_value=40,
                                           value=cur_fdef.get("rows", auto_rows),
                                           key=f"rows_{field_name}")

        # Precision cell detection with sub-pixel contour snapping
        raw_dets, _, actual_dims = detect_bubbles_in_roi(
            warped_img,
            roi_rect=(roi_x, roi_y, roi_w, roi_h),
            target_shape=chosen_shape,
            expected_cols=int(num_cols),
            expected_rows=int(num_rows),
            use_lattice_engine=True,
            auto_detect_grid=auto_dim_toggle
        )

        clean_dets = raw_dets
        if orientation == "Horizontal":
            grouped = group_into_rows(clean_dets)
        else:
            grouped = group_into_columns(clean_dets)

        total_bubbles = sum(len(g) for g in grouped)
        num_items = len(grouped)
        options_per_q = len(grouped[0]) if grouped else int(num_cols if orientation == "Horizontal" else num_rows)

        # ----------------------------------------------------------------------
        # 1. EXTRACT INFORMATION DISPLAY (USER REQUEST)
        # ----------------------------------------------------------------------
        if field_type == "multiple_choice":
            st.info(f"📊 **Ekstraksi Soal (Kelompok):** Menghasilkan **{num_items} Soal / Item** (Tiap soal memiliki **{options_per_q} pilihan jawaban**, Total: **{total_bubbles} kotak**)")
        elif field_type == "text":
            st.info(f"📊 **Ekstraksi Teks (1 Variabel Gabungan):** Menghasilkan **1 Kolom Variabel (`{field_name}`)** berisi gabungan **{num_items} karakter huruf** (Total: **{total_bubbles} kotak**)")
        elif field_type == "number":
            st.info(f"📊 **Ekstraksi Angka (1 Variabel Gabungan):** Menghasilkan **1 Kolom Variabel (`{field_name}`)** berisi gabungan **{num_items} digit angka** (Total: **{total_bubbles} kotak**)")
        else:
            st.info(f"📊 **Ekstraksi Pilihan Tunggal (1 Variabel):** Menghasilkan **1 Kolom Variabel (`{field_name}`)** dengan **{options_per_q} opsi** (Total: **{total_bubbles} kotak**)")

        # ----------------------------------------------------------------------
        # 2. LABELS PER CELL CONFIGURATION (USER REQUEST)
        # ----------------------------------------------------------------------
        if cur_fdef.get("labels"):
            def_labels_pool = cur_fdef["labels"]
        elif cur_fdef.get("items") and cur_fdef["items"][0].get("bubbles"):
            def_labels_pool = [b["option"] for b in cur_fdef["items"][0]["bubbles"]]
        elif field_type == "text":
            def_labels_pool = [chr(65 + i) for i in range(26)]
        elif field_type == "number":
            def_labels_pool = [str(i) for i in range(10)]
        else:
            alpha = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
            def_labels_pool = [alpha[i] for i in range(min(max(1, options_per_q), len(alpha)))]

        def_label_str = ", ".join(def_labels_pool)
        custom_label_input = st.text_input(
            "🔤 Label Pilihan Jawaban per Sel (pisahkan koma):",
            value=def_label_str,
            key=f"labels_{field_name}",
            help="Default isi pilihan per sel (misal: A, B, C, D atau 1, 2, 3, 4)"
        )
        active_labels_pool = [x.strip() for x in custom_label_input.split(",") if x.strip()]
        if not active_labels_pool:
            active_labels_pool = def_labels_pool

        # ----------------------------------------------------------------------
        # 3. VARIABLE & COLUMN NAMING (USER REQUEST)
        # multiple_choice: Berkelompok soal dengan prefix & urutan start-end
        # text / number / choice: 1 variabel tunggal gabungan (nama section)
        # ----------------------------------------------------------------------
        if field_type == "multiple_choice":
            st.markdown("##### 🏷️ Penamaan Kolom Soal Berkelompok (1-75):")

            saved_prefix = cur_fdef.get("prefix")
            if saved_prefix is None:
                saved_prefix = "soal_"

            saved_start = cur_fdef.get("start_idx", 1)
            saved_pad = cur_fdef.get("pad_zero", False)

            c_v1, c_v2, c_v3 = st.columns([2, 1.5, 1.5])
            with c_v1:
                prefix_input = st.text_input(
                    "Awalan (Prefix):",
                    value=saved_prefix,
                    key=f"prefix_{field_name}",
                    help="Awalan nama kolom hasil scan (contoh: soal_, Q, nomor_, atau kosongkan)"
                )
            with c_v2:
                start_num = st.number_input(
                    "Nomor Awal (Start):",
                    min_value=1,
                    max_value=9999,
                    value=int(saved_start),
                    key=f"start_{field_name}",
                    help="Nomor urut soal pertama dalam section ini (misal: 1, 26, 51)"
                )
            with c_v3:
                end_num = start_num + num_items - 1
                st.metric("Nomor Akhir (End):", f"{end_num}")

            pad_zero = st.checkbox(
                "Format angka 2 digit (01, 02 vs 1, 2)",
                value=saved_pad,
                key=f"pad_{field_name}"
            )

            def format_col_name(num):
                num_str = f"{num:02d}" if pad_zero else f"{num}"
                return f"{prefix_input}{num_str}"

            sample_cols = [format_col_name(start_num + i) for i in range(num_items)]
            if len(sample_cols) <= 4:
                preview_str = ", ".join(sample_cols)
            else:
                preview_str = f"{sample_cols[0]}, {sample_cols[1]}, {sample_cols[2]}, ... , {sample_cols[-1]}"

            st.caption(f"💡 **Preview Kolom Hasil:** `{preview_str}` (Total: {num_items} variabel kolom)")
        else:
            # text, number, choice -> Satu variabel tunggal hasil penggabungan
            prefix_input = ""
            start_num = 1
            end_num = 1
            pad_zero = False
            unit_name = "karakter huruf" if field_type == "text" else ("digit angka" if field_type == "number" else "opsi pilihan")
            st.markdown(f"##### 🏷️ Output Variabel Tunggal: **`{field_name}`**")
            st.success(f"🔗 **1 Kolom Variabel Hasil:** Seluruh {num_items} {unit_name} otomatis digabung menjadi satu variabel bernama **`{field_name}`** pada hasil pembacaan scan dan export CSV/JSON.")

        formatted_items = []
        for idx, grp in enumerate(grouped):
            if field_type == "multiple_choice":
                curr_q_num = start_num + idx
                item_label = format_col_name(curr_q_num)
            elif field_type == "choice":
                curr_q_num = 1
                item_label = field_name
            elif field_type == "text":
                curr_q_num = idx + 1
                item_label = f"{field_name}_col{idx + 1}"
            else:  # number
                curr_q_num = idx + 1
                item_label = f"{field_name}_col{idx + 1}"

            item_bubbles = []
            for b_idx, b in enumerate(grp):
                opt_label = active_labels_pool[b_idx] if b_idx < len(active_labels_pool) else str(b_idx + 1)
                bw = round(float(b.get("w", 24)), 2)
                bh = round(float(b.get("h", 24)), 2)
                cx = round(float(b["cx"]), 2)
                cy = round(float(b["cy"]), 2)
                bx = int(round(b.get("x", cx - bw / 2)))
                by = int(round(b.get("y", cy - bh / 2)))

                item_bubbles.append({
                    "x": bx,
                    "y": by,
                    "w": int(bw),
                    "h": int(bh),
                    "cx": cx,
                    "cy": cy,
                    "radius": round(float(b.get("radius", min(bw, bh) / 2.0)), 2),
                    "shape": chosen_shape,
                    "option": opt_label,
                    "col": int(b.get("col", b_idx)),
                    "row": int(b.get("row", idx))
                })

            formatted_items.append({
                "index": curr_q_num,
                "name": item_label,
                "bubbles": item_bubbles
            })

        # AUTO-SYNC: Immediately update active field into calibrated_fields so summary is ALWAYS 100% dynamic
        flat_cells = [b for it in formatted_items for b in it["bubbles"]]
        st.session_state["calibrated_fields"][field_name] = {
            "field_name": field_name,
            "field_type": field_type,
            "orientation": orientation,
            "start_idx": int(start_num),
            "end_idx": int(end_num),
            "prefix": str(prefix_input),
            "pad_zero": bool(pad_zero),
            "labels": active_labels_pool,
            "num_questions": int(num_items if field_type == "multiple_choice" else 1),
            "options_per_item": int(options_per_q),
            "roi": [roi_x, roi_y, roi_w, roi_h],
            "cols": int(num_cols),
            "rows": int(num_rows),
            "item_count": len(formatted_items),
            "cells": flat_cells,
            "items": formatted_items
        }

        overlay_view = draw_field_overlay(
            warped_img,
            formatted_items,
            orientation=orientation,
            color=(0, 0, 255),
            show_labels=False,
            draw_outer_box=True
        )

        # Zoomed preview of detected grid directly on the right
        cx1 = max(0, roi_x - 15)
        cy1 = max(0, roi_y - 15)
        cx2 = min(canvas_w, roi_x + roi_w + 15)
        cy2 = min(canvas_h, roi_y + roi_h + 15)
        zoomed_overlay = overlay_view[cy1:cy2, cx1:cx2]
        st.image(cv_to_pil(zoomed_overlay), caption=f"🎯 Preview Deteksi: {field_name} ({total_bubbles} kotak terpetakan 100%!)", use_container_width=True)

        # Section Action Buttons (Compact)
        col_act1, col_act2, col_act3 = st.columns([2, 1, 1])
        with col_act1:
            if st.button(f"💾 Simpan Section ({total_bubbles} Sel)", type="primary", use_container_width=True):
                st.toast(f"✅ Section '{field_name}' ({total_bubbles} kotak) tersimpan permanen!", icon="💾")
                st.rerun()

        with col_act2:
            if st.button("📑 Duplikat", use_container_width=True, help="Duplikat section ini untuk kolom/blok soal berikutnya"):
                if field_type == "multiple_choice":
                    new_start = end_num + 1
                    new_end = new_start + num_items - 1
                    if field_name.lower().startswith("soal_"):
                        dup_name = f"Soal_{new_start}_{new_end}"
                    else:
                        dup_name = f"{field_name}_copy"
                else:
                    new_start = 1
                    new_end = 1
                    dup_name = f"{field_name}_copy"

                while dup_name in st.session_state["calibrated_fields"]:
                    dup_name = f"{dup_name}_copy"

                new_x = roi_x + roi_w + 40 if (roi_x + roi_w * 2 + 40) <= canvas_w else roi_x
                new_y = roi_y if (roi_x + roi_w * 2 + 40) <= canvas_w else min(canvas_h - roi_h, roi_y + roi_h + 30)
                dx = new_x - roi_x
                dy = new_y - roi_y

                dup_items = []
                for idx_c, it in enumerate(formatted_items):
                    it_copy = dict(it)
                    if field_type == "multiple_choice":
                        curr_q = new_start + idx_c
                        q_str = f"{curr_q:02d}" if pad_zero else f"{curr_q}"
                        it_copy["index"] = curr_q
                        it_copy["name"] = f"{prefix_input}{q_str}"
                    else:
                        it_copy["name"] = it["name"]

                    it_copy["bubbles"] = [
                        {
                            **b,
                            "x": b["x"] + dx,
                            "y": b["y"] + dy,
                            "cx": round(b["cx"] + dx, 2),
                            "cy": round(b["cy"] + dy, 2)
                        }
                        for b in it["bubbles"]
                    ]
                    dup_items.append(it_copy)

                flat_dup_cells = [b for it in dup_items for b in it["bubbles"]]

                st.session_state["calibrated_fields"][dup_name] = {
                    "field_name": dup_name,
                    "field_type": field_type,
                    "orientation": orientation,
                    "start_idx": int(new_start),
                    "end_idx": int(new_end),
                    "prefix": str(prefix_input),
                    "pad_zero": bool(pad_zero),
                    "labels": active_labels_pool,
                    "num_questions": int(num_items if field_type == "multiple_choice" else 1),
                    "options_per_item": int(options_per_q),
                    "roi": [new_x, new_y, roi_w, roi_h],
                    "cols": int(num_cols),
                    "rows": int(num_rows),
                    "item_count": len(dup_items),
                    "cells": flat_dup_cells,
                    "items": dup_items
                }
                st.session_state[f"active_box_{dup_name}"] = [new_x, new_y, roi_w, roi_h]
                if f"roi_editor_{dup_name}" in st.session_state:
                    del st.session_state[f"roi_editor_{dup_name}"]
                st.session_state["editing_field_name"] = dup_name
                st.toast(f"📑 Section '{field_name}' diduplikat sebagai '{dup_name}'!", icon="📋")
                st.rerun()

        with col_act3:
            if field_name in st.session_state["calibrated_fields"] and len(st.session_state["calibrated_fields"]) > 1:
                if st.button("🗑️ Hapus", use_container_width=True, help="Hapus section yang dipilih"):
                    del st.session_state["calibrated_fields"][field_name]
                    if box_state_key in st.session_state:
                        del st.session_state[box_state_key]
                    if comp_state_key in st.session_state:
                        del st.session_state[comp_state_key]
                    rem = list(st.session_state["calibrated_fields"].keys())
                    st.session_state["editing_field_name"] = rem[0]
                    st.toast(f"🗑️ Section '{field_name}' dihapus!", icon="🗑️")
                    st.rerun()

    with col_canvas:
        st.markdown(f"##### 🖥️ Kanvas LJK: **{field_name}** ({'⏹️ Kotak' if chosen_shape == 'square' else '🟢 Bulatan'})")
        st.caption("Tarik & ubah ukuran kotak langsung pada lembar kerja:")

        updated_box = render_roi_editor(
            warped_img,
            box=current_box,
            label=f"{field_name} [{num_cols}x{num_rows}]",
            key=comp_state_key
        )

        if updated_box and isinstance(updated_box, dict) and "x" in updated_box:
            if (
                updated_box["x"] != current_box[0] or
                updated_box["y"] != current_box[1] or
                updated_box["w"] != current_box[2] or
                updated_box["h"] != current_box[3]
            ):
                st.session_state[box_state_key] = [
                    int(updated_box["x"]), int(updated_box["y"]), int(updated_box["w"]), int(updated_box["h"])
                ]
                current_box = st.session_state[box_state_key]
                st.rerun()

    # --------------------------------------------------------------------------
    # FULL TEMPLATE SUMMARY TABLE: 100% DYNAMIC WITH INLINE EDIT, DUP & DEL
    # --------------------------------------------------------------------------
    st.divider()
    st.header("📦 Ringkasan Template Lengkap")

    if st.session_state["calibrated_fields"]:
        col_sum_left, col_sum_right = st.columns([3, 2], gap="medium")

        with col_sum_left:
            st.markdown("##### 📋 Tabel Daftar Section Terkonfigurasi:")

            # Table Header
            col_h_no, col_h_name, col_h_type, col_h_var, col_h_grid, col_h_cells, col_h_actions = st.columns([0.4, 2.4, 1.3, 2.7, 1.3, 0.9, 2.5])
            with col_h_no:
                st.markdown("**No**")
            with col_h_name:
                st.markdown("**Nama Section (✏️ Rename)**")
            with col_h_type:
                st.markdown("**Tipe**")
            with col_h_var:
                st.markdown("**Rentang Soal / Variabel**")
            with col_h_grid:
                st.markdown("**Grid**")
            with col_h_cells:
                st.markdown("**Sel**")
            with col_h_actions:
                st.markdown("<div style='text-align:center;'><b>Tindakan</b></div>", unsafe_allow_html=True)

            st.markdown("<hr style='margin: 4px 0px 8px 0px; border-top: 2px solid #555;'>", unsafe_allow_html=True)

            # Table Rows
            for idx, (fn, fd) in enumerate(list(st.session_state["calibrated_fields"].items())):
                tot_b = sum(len(it["bubbles"]) for it in fd.get("items", []))
                is_active = (fn == st.session_state.get("editing_field_name"))

                c_no, c_name, c_type, c_var, c_grid, c_cells, c_actions = st.columns([0.4, 2.4, 1.3, 2.7, 1.3, 0.9, 2.5])
                with c_no:
                    st.markdown(f"<div style='padding-top: 6px;'>{idx + 1}</div>", unsafe_allow_html=True)

                with c_name:
                    if is_active:
                        cn_badge, cn_box = st.columns([0.35, 2.85])
                        with cn_badge:
                            st.markdown("<div style='padding-top: 6px; font-size: 15px;' title='Sedang aktif diedit di atas'>🟢</div>", unsafe_allow_html=True)
                        with cn_box:
                            new_name_val = st.text_input(
                                f"Rename {fn}",
                                value=fn,
                                key=f"inline_rename_{fn}",
                                label_visibility="collapsed",
                                help=f"Ketik untuk mengganti nama section '{fn}' secara instan (tekan Enter)"
                            )
                    else:
                        new_name_val = st.text_input(
                            f"Rename {fn}",
                            value=fn,
                            key=f"inline_rename_{fn}",
                            label_visibility="collapsed",
                            help=f"Ketik untuk mengganti nama section '{fn}' secara instan (tekan Enter)"
                        )

                    # Dynamic inline rename trigger
                    if new_name_val and new_name_val.strip() != fn:
                        cand_name = new_name_val.strip()
                        if cand_name in st.session_state["calibrated_fields"]:
                            st.warning(f"Nama '{cand_name}' sudah ada!")
                        else:
                            reordered = {}
                            for k, v in st.session_state["calibrated_fields"].items():
                                if k == fn:
                                    v_renamed = dict(v)
                                    v_renamed["field_name"] = cand_name
                                    reordered[cand_name] = v_renamed
                                else:
                                    reordered[k] = v
                            st.session_state["calibrated_fields"] = reordered

                            if f"active_box_{fn}" in st.session_state:
                                st.session_state[f"active_box_{cand_name}"] = st.session_state.pop(f"active_box_{fn}")
                            if f"roi_editor_{fn}" in st.session_state:
                                del st.session_state[f"roi_editor_{fn}"]

                            if st.session_state.get("editing_field_name") == fn:
                                st.session_state["editing_field_name"] = cand_name

                            st.toast(f"✏️ Section '{fn}' diubah namanya menjadi '{cand_name}'!", icon="✏️")
                            st.rerun()

                with c_type:
                    st.markdown(f"<div style='padding-top: 6px;'><code>{fd.get('field_type', 'mc')}</code></div>", unsafe_allow_html=True)

                with c_var:
                    ft = fd.get("field_type", "multiple_choice")
                    opts_sample = ", ".join(fd.get("labels", ["A", "B", "C", "D"])[:5])
                    if ft == "multiple_choice":
                        var_start = fd.get("start_idx", 1)
                        var_end = fd.get("end_idx", var_start + len(fd.get("items", [])) - 1)
                        var_prefix = fd.get("prefix", "soal_")
                        var_pad = fd.get("pad_zero", False)
                        s_str = f"{var_start:02d}" if var_pad else f"{var_start}"
                        e_str = f"{var_end:02d}" if var_pad else f"{var_end}"
                        st.markdown(
                            f"<div style='padding-top: 4px;'><b>🏷️ {var_prefix}{s_str} s/d {var_prefix}{e_str}</b><br><small style='color:#aaa;'>({len(fd.get('items', []))} soal • opsi: {opts_sample})</small></div>",
                            unsafe_allow_html=True
                        )
                    elif ft == "text":
                        st.markdown(
                            f"<div style='padding-top: 4px;'><b>🔗 1 Kolom: <code>{fn}</code></b><br><small style='color:#aaa;'>(Teks gabungan {len(fd.get('items', []))} huruf A-Z)</small></div>",
                            unsafe_allow_html=True
                        )
                    elif ft == "number":
                        st.markdown(
                            f"<div style='padding-top: 4px;'><b>🔗 1 Kolom: <code>{fn}</code></b><br><small style='color:#aaa;'>(Angka gabungan {len(fd.get('items', []))} digit 0-9)</small></div>",
                            unsafe_allow_html=True
                        )
                    else:  # choice
                        st.markdown(
                            f"<div style='padding-top: 4px;'><b>🔗 1 Kolom: <code>{fn}</code></b><br><small style='color:#aaa;'>(Pilihan tunggal • opsi: {opts_sample})</small></div>",
                            unsafe_allow_html=True
                        )

                with c_grid:
                    st.markdown(f"<div style='padding-top: 6px;'>{fd.get('cols', '-')}K × {fd.get('rows', '-')}B</div>", unsafe_allow_html=True)

                with c_cells:
                    st.markdown(f"<div style='padding-top: 6px;'>{tot_b}</div>", unsafe_allow_html=True)

                with c_actions:
                    ca1, ca2, ca3 = st.columns([1, 1, 1])
                    with ca1:
                        if st.button("✏️", key=f"row_edit_{fn}", help=f"Edit area section '{fn}' di atas", use_container_width=True):
                            st.session_state["editing_field_name"] = fn
                            st.session_state[f"active_box_{fn}"] = list(fd.get("roi", [150, 150, 420, 500]))
                            if f"roi_editor_{fn}" in st.session_state:
                                del st.session_state[f"roi_editor_{fn}"]
                            st.toast(f"✏️ Memuat section '{fn}' ke editor!", icon="✏️")
                            st.rerun()

                    with ca2:
                        if st.button("📑", key=f"row_dup_{fn}", help=f"Duplikat section '{fn}'", use_container_width=True):
                            ft = fd.get("field_type", "multiple_choice")
                            item_cnt = len(fd.get("items", []))
                            if ft == "multiple_choice":
                                prev_start = fd.get("start_idx", 1)
                                new_start = prev_start + item_cnt
                                new_end = new_start + item_cnt - 1
                                dup_prefix = fd.get("prefix", "soal_")
                                dup_pad = fd.get("pad_zero", False)
                                if fn.lower().startswith("soal_"):
                                    new_fn = f"Soal_{new_start}_{new_end}"
                                else:
                                    new_fn = f"{fn}_copy"
                            else:
                                new_start = 1
                                new_end = 1
                                dup_prefix = ""
                                dup_pad = False
                                new_fn = f"{fn}_copy"

                            while new_fn in st.session_state["calibrated_fields"]:
                                new_fn = f"{new_fn}_copy"

                            rx, ry, rw, rh = fd["roi"]
                            nx = rx + rw + 40 if (rx + rw * 2 + 40) <= canvas_w else rx
                            ny = ry if (rx + rw * 2 + 40) <= canvas_w else min(canvas_h - rh, ry + rh + 30)
                            dx = nx - rx
                            dy = ny - ry

                            cloned_items = []
                            for idx_c, it in enumerate(fd.get("items", [])):
                                it_c = dict(it)
                                if ft == "multiple_choice":
                                    curr_q = new_start + idx_c
                                    q_str = f"{curr_q:02d}" if dup_pad else f"{curr_q}"
                                    it_c["index"] = curr_q
                                    it_c["name"] = f"{dup_prefix}{q_str}"
                                else:
                                    it_c["name"] = it["name"]

                                it_c["bubbles"] = [
                                    {
                                        **b,
                                        "x": b.get("x", int(b["cx"] - b.get("w", 24) / 2)) + dx,
                                        "y": b.get("y", int(b["cy"] - b.get("h", 24) / 2)) + dy,
                                        "cx": round(b["cx"] + dx, 2),
                                        "cy": round(b["cy"] + dy, 2)
                                    }
                                    for b in it["bubbles"]
                                ]
                                cloned_items.append(it_c)

                            flat_cloned_cells = [b for it in cloned_items for b in it["bubbles"]]

                            st.session_state["calibrated_fields"][new_fn] = {
                                **fd,
                                "field_name": new_fn,
                                "start_idx": int(new_start),
                                "end_idx": int(new_end),
                                "prefix": dup_prefix,
                                "pad_zero": dup_pad,
                                "labels": fd.get("labels", []),
                                "num_questions": item_cnt if ft == "multiple_choice" else 1,
                                "roi": [nx, ny, rw, rh],
                                "cells": flat_cloned_cells,
                                "items": cloned_items
                            }
                            st.session_state[f"active_box_{new_fn}"] = [nx, ny, rw, rh]
                            if f"roi_editor_{new_fn}" in st.session_state:
                                del st.session_state[f"roi_editor_{new_fn}"]
                            st.session_state["editing_field_name"] = new_fn
                            st.toast(f"📑 Section '{fn}' diduplikat sebagai '{new_fn}'!", icon="📋")
                            st.rerun()

                    with ca3:
                        if len(st.session_state["calibrated_fields"]) > 1:
                            if st.button("🗑️", key=f"row_del_{fn}", help=f"Hapus section '{fn}'", use_container_width=True):
                                del st.session_state["calibrated_fields"][fn]
                                if f"active_box_{fn}" in st.session_state:
                                    del st.session_state[f"active_box_{fn}"]
                                if f"roi_editor_{fn}" in st.session_state:
                                    del st.session_state[f"roi_editor_{fn}"]
                                rem = list(st.session_state["calibrated_fields"].keys())
                                st.session_state["editing_field_name"] = rem[0]
                                st.toast(f"🗑️ Section '{fn}' berhasil dihapus!", icon="🗑️")
                                st.rerun()

                st.markdown("<hr style='margin: 2px 0px 4px 0px; border-top: 1px solid #333;'>", unsafe_allow_html=True)

            if st.button("🗑️ Reset Seluruh Field (Hapus Semua)"):
                st.session_state["calibrated_fields"] = {}
                st.rerun()

        with col_sum_right:
            full_overlay = draw_all_fields_overlay(warped_img, st.session_state["calibrated_fields"])
            st.image(cv_to_pil(full_overlay), caption="Visualisasi Seluruh Field pada Template", use_container_width=True)

            template_export = {
                "version": "2.0",
                "canvas": {"width": canvas_w, "height": canvas_h},
                "bubble_shape": chosen_shape,
                "alignment_method": method_used,
                "aruco_dict": detected_dict,
                "aruco_corner_ids": corner_ids,
                "crop_mode": c_mode,
                "regmarks": ordered_pts.tolist(),
                "fields": st.session_state["calibrated_fields"]
            }

            json_bytes = json.dumps(template_export, indent=2)
            st.download_button(
                label="⬇️ Download template.json Lengkap (Termasuk Koordinat Tiap Cell)",
                data=json_bytes,
                file_name="template.json",
                mime="application/json",
                type="primary",
                use_container_width=True
            )
    else:
        st.info("Belum ada field yang disimpan. Silakan sesuaikan area di atas dan klik 'Simpan Section'.")


# ==============================================================================
# MODE ADMIN 2: OMR READER (BATCH EVALUATOR LENGKAP)
# ==============================================================================
elif mode == "📊 OMR Reader & Batch Evaluator Lengkap (Admin)":
    st.markdown("""
    <div style="margin-bottom: 12px;">
        <span style="font-size: 11px; font-weight: 700; color: #BA0C2F; text-transform: uppercase; letter-spacing: 0.08em;">OMR READER & EVALUATOR (MODE ADMIN)</span>
        <h2 style="font-size: 22px; font-weight: 800; color: #0F172A; margin: 2px 0 6px 0;">📑 Batch Evaluator & Deteksi Jawaban Lengkap (Silang 'X' & Arsiran)</h2>
        <p style="font-size: 13px; color: #475569; margin: 0;">Evaluasi otomatis lembar jawaban komputer berkecepatan tinggi dengan analisis diferensial baseline tinta dan konfigurasi lanjutan.</p>
    </div>
    """, unsafe_allow_html=True)

    col_up1, col_up2 = st.columns([1.1, 1.9])
    with col_up1:
        st.markdown("#### 1. Template LJK")
        tpl_source = st.radio(
            "Pilih Template OMR:",
            options=[
                "⭐ template-final.json (Default Telkom University)",
                "📤 Upload Template JSON Kustom"
            ],
            index=0,
            help="template-final.json adalah template resmi untuk ujian Telkom University (11 Section, 75 Soal)."
        )

        template = None
        if tpl_source == "⭐ template-final.json (Default Telkom University)":
            template = load_default_template()
            if template:
                sec_list = list(template.get("fields", {}).keys())
                st.markdown(f"""
                <div style="background-color: #FFF1F2; border: 1px solid #FECDD3; border-radius: 8px; padding: 12px; margin-top: 6px;">
                    <div style="font-weight: 700; color: #BA0C2F; font-size: 13px;">⭐ Terpilih: template-final.json</div>
                    <div style="font-size: 12px; color: #334155; margin-top: 4px; line-height: 1.5;">
                        • <b>{len(sec_list)} Section Aktif</b>: {', '.join(sec_list[:5])}...<br>
                        • <b>75 Soal Pilihan Ganda</b> (Soal A s/d E)<br>
                        • Format: <b>Kotak (Square)</b> &bull; ArUco: <b>DICT_4X4_50</b>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.error("File template-final.json tidak ditemukan di direktori aplikasi.")
        else:
            template_file = st.file_uploader("Upload file template.json:", type=["json"])
            if template_file:
                try:
                    template = json.load(template_file)
                    st.success(f"Template '{template_file.name}' berhasil dimuat!")
                except Exception as e:
                    st.error(f"Gagal membaca file JSON: {e}")

    with col_up2:
        st.markdown("#### 2. Berkas LJK Peserta")
        uploaded_files = st.file_uploader(
            "Upload Lembar Jawaban (PDF Multi-Halaman / JPG / PNG / HEIC iPhone):",
            type=["pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"],
            accept_multiple_files=True,
            help="Mendukung PDF multi-halaman maupun kumpulan gambar scan / foto iPhone sekaligus."
        )

        # Quick test helper if test file exists
        test_pdf_path = os.path.join(os.path.dirname(__file__), "filled_LJK.xlsx.pdf")
        if not uploaded_files and os.path.exists(test_pdf_path):
            if st.button("📄 Uji Coba Cepat: Muat filled_LJK.xlsx.pdf", use_container_width=True):
                with open(test_pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                    setattr(pdf_bytes, "name", "filled_LJK.xlsx.pdf")
                    uploaded_files = [pdf_bytes]
                st.session_state["quick_test_files"] = uploaded_files
                st.rerun()

        if not uploaded_files and st.session_state.get("quick_test_files"):
            uploaded_files = st.session_state["quick_test_files"]

    st.sidebar.subheader("⚙️ Sensitivitas Pembacaan Jawaban")
    read_profile = st.sidebar.radio(
        "Profil Deteksi Jawaban:",
        options=[
            "🎯 Otomatis (Tanda Silang 'X' & Arsiran Pensil/Pulpen)",
            "✏️ Tanda Silang 'X' Halus / Tipis (Ekstra Sensitif)",
            "⬛ Arsiran Pensil 2B Penuh"
        ],
        index=0,
        help="Algoritma diferensial baseline membandingkan rasio tinta di dalam kotak terhadap opsi lainnya dalam soal tersebut."
    )

    default_thresh = 0.28
    if "Ekstra Sensitif" in read_profile:
        default_thresh = 0.20
    elif "Pensil 2B" in read_profile:
        default_thresh = 0.32

    read_thresh = st.sidebar.slider("Fill Ratio Threshold", 0.05, 0.45, default_thresh, 0.01,
                                    help="Batas ambang kepekatan tanda. Nilai lebih rendah (0.10-0.16) sangat sensitif untuk tanda silang tipis.")
    ambig_margin = st.sidebar.slider("Margin Ganda (Ambiguity Margin)", 0.03, 0.18, 0.08, 0.01,
                                     help="Selisih minimal antara opsi teratas dan opsi kedua untuk dianggap jawaban tunggal.")

    if template and uploaded_files:
        canvas_w = template.get("canvas", {}).get("width", 1700)
        canvas_h = template.get("canvas", {}).get("height", 2400)
        fields_dict = template.get("fields", {})
        tpl_shape = template.get("bubble_shape", "square")
        align_method = template.get("alignment_method", "aruco")
        aruco_dict = template.get("aruco_dict", "DICT_4X4_50")
        expected_ids = template.get("aruco_corner_ids")
        crop_m = template.get("crop_mode", "inner")

        st.markdown(f"""
        <div style="background-color: #ECFDF5; border: 1px solid #A7F3D0; border-radius: 8px; padding: 10px 16px; margin: 12px 0;">
            <div style="font-weight: 700; color: #065F46; font-size: 13px;">✅ Siap Memproses {len(uploaded_files)} Berkas</div>
            <div style="font-size: 12px; color: #047857; margin-top: 2px;">
                Template: <b>{len(fields_dict)} field</b> &bull; Bentuk: <b>{'Kotak (Square)' if tpl_shape == 'square' else 'Bulatan'}</b> &bull; ArUco: <b>{aruco_dict}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

        if st.button("🚀 Mulai Pemrosesan Batch", type="primary", use_container_width=True):
            all_results = []
            preview_images = []

            all_pages_to_process = []
            with st.spinner("Mengekstrak seluruh halaman dokumen & PDF..."):
                for uf in uploaded_files:
                    try:
                        pages = extract_images_from_file(uf, target_dpi=200)
                        all_pages_to_process.extend(pages)
                    except Exception as e:
                        st.error(f"Error memproses berkas {uf.name}: {str(e)}")

            st.info(f"Total lembar yang diproses: **{len(all_pages_to_process)} halaman**.")
            progress_bar = st.progress(0)

            for idx, (doc_name, img_bgr) in enumerate(all_pages_to_process):
                warped, pts, method, _, _, status = detect_corners_and_crop(
                    img_bgr,
                    canvas_w=canvas_w,
                    canvas_h=canvas_h,
                    preferred_method=align_method,
                    expected_ids=expected_ids,
                    dict_name=aruco_dict,
                    crop_mode=crop_m
                )

                row_result = {
                    "File / Halaman": doc_name,
                    "Corner Status": status,
                    "Alignment Method": method
                }

                gray_warped = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)

                for fname, fdef in fields_dict.items():
                    fdef_copy = dict(fdef)
                    if "field_name" not in fdef_copy:
                        fdef_copy["field_name"] = fname
                    field_data = decode_field(gray_warped, fdef_copy, thresh=read_thresh, margin=ambig_margin)
                    row_result.update(field_data)

                overlay_img = draw_reading_overlay(warped, fields_dict, gray_warped, thresh=read_thresh)
                preview_images.append((doc_name, overlay_img))

                all_results.append(row_result)
                progress_bar.progress((idx + 1) / len(all_pages_to_process))

            st.session_state["reader_results"] = all_results
            st.session_state["reader_previews"] = preview_images

        if "reader_results" in st.session_state:
            results_df = pd.DataFrame(st.session_state["reader_results"])
            st.subheader("📊 Tabel Hasil Pembacaan")
            st.dataframe(results_df, use_container_width=True)

            col_exp1, col_exp2 = st.columns(2)
            with col_exp1:
                csv_data = export_to_csv(st.session_state["reader_results"])
                st.download_button("⬇️ Download CSV", data=csv_data, file_name="omr_results.csv", mime="text/csv", use_container_width=True)
            with col_exp2:
                json_data = export_to_json(st.session_state["reader_results"])
                st.download_button("⬇️ Download JSON", data=json_data, file_name="omr_results.json", mime="application/json", use_container_width=True)

            st.divider()
            st.subheader("🔍 Visualisasi Overlay Hasil (Hijau = Jawaban Terdeteksi)")
            selected_file = st.selectbox("Pilih Lembar untuk Diinspeksi:", [p[0] for p in st.session_state["reader_previews"]])
            for p in st.session_state["reader_previews"]:
                if p[0] == selected_file:
                    st.image(cv_to_pil(p[1]), caption=f"Visualisasi Overlay: {p[0]} (Kotak Hijau & Titik Tengah = Jawaban Tersilang/Terarsir, Merah = Ganda)", use_container_width=True)
