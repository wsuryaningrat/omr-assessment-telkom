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
import re
import time
from datetime import datetime, timezone, timedelta
from PIL import Image
import streamlit.components.v1 as components
from core.alignment import (
    find_aruco_markers,
    find_regmarks,
    detect_corners_and_crop,
    perspective_warp,
    draw_regmarks_overlay,
    draw_cropped_coordinate_system_overlay,
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
from core.pdf_utils import extract_images_from_file, iter_images_from_file

# Sisi terpanjang maksimum foto saat di-decode (canvas template 1700x2400).
SCAN_MAX_SIDE = 2400
from core.utils import (
    draw_field_overlay,
    draw_all_fields_overlay,
    draw_reading_overlay,
    export_to_csv,
    export_to_json,
)
from core.evaluator import (
    parse_kunci_jawaban_excel,
    load_kunci_jawaban_from_gsheet,
    find_matching_kunci_sheet,
    grade_student_record,
    generate_sample_kunci_excel
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

def get_default_template_path():
    """Finds the default template path: checks templates/ folder first, then fallback to template-final.json."""
    base_dir = os.path.dirname(__file__)
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

/* Sembunyikan batas ukuran dan format berkas di seluruh file uploader */
[data-testid="stFileUploaderDropzoneInstructions"] small,
section[data-testid="stFileUploaderDropzone"] small,
div[data-testid="stFileUploader"] section small,
div[data-testid="stFileUploader"] small,
.stFileUploader small {
    display: none !important;
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

/* Primary Button Styling (Telkom Red) & Solid White Text */
button[kind="primary"],
button[data-testid="stBaseButton-primary"],
div[data-testid="stButton"] button[kind="primary"],
div[data-testid="stLinkButton"] a[kind="primary"],
div[data-testid="stLinkButton"] a[data-testid="stBaseButton-primary"] {
    background-color: #BA0C2F !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 700 !important;
    box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05) !important;
    transition: all 0.2s ease !important;
}

button[kind="primary"] *,
button[kind="primary"] p,
button[kind="primary"] span,
button[kind="primary"] div,
button[data-testid="stBaseButton-primary"] *,
button[data-testid="stBaseButton-primary"] p,
button[data-testid="stBaseButton-primary"] span,
div[data-testid="stButton"] button[kind="primary"] *,
div[data-testid="stButton"] button[kind="primary"] p,
div[data-testid="stButton"] button[kind="primary"] span,
div[data-testid="stLinkButton"] a[kind="primary"] *,
div[data-testid="stLinkButton"] a[kind="primary"] p,
div[data-testid="stLinkButton"] a[kind="primary"] span,
div[data-testid="stLinkButton"] a[data-testid="stBaseButton-primary"] *,
div[data-testid="stLinkButton"] a[data-testid="stBaseButton-primary"] p,
div[data-testid="stLinkButton"] a[data-testid="stBaseButton-primary"] span {
    color: #FFFFFF !important;
    -webkit-text-fill-color: #FFFFFF !important;
    fill: #FFFFFF !important;
    font-weight: 700 !important;
}

button[kind="primary"]:hover,
button[data-testid="stBaseButton-primary"]:hover,
div[data-testid="stButton"] button[kind="primary"]:hover,
div[data-testid="stLinkButton"] a[kind="primary"]:hover {
    background-color: #980925 !important;
    color: #FFFFFF !important;
    box-shadow: 0 4px 8px -1px rgba(186, 12, 47, 0.3) !important;
}

button[kind="primary"]:hover *,
button[data-testid="stBaseButton-primary"]:hover *,
div[data-testid="stButton"] button[kind="primary"]:hover *,
div[data-testid="stLinkButton"] a[kind="primary"]:hover * {
    color: #FFFFFF !important;
    -webkit-text-fill-color: #FFFFFF !important;
}

button[kind="primary"]:active,
button[data-testid="stBaseButton-primary"]:active {
    background-color: #7B061D !important;
}

/* Secondary Button Styling */
button[kind="secondary"],
div[data-testid="stLinkButton"] a[kind="secondary"] {
    border-radius: 8px !important;
    border: 1px solid #CBD5E1 !important;
    font-weight: 600 !important;
    color: #1E293B !important;
    background-color: #FFFFFF !important;
    transition: all 0.2s ease !important;
}
button[kind="secondary"]:hover,
div[data-testid="stLinkButton"] a[kind="secondary"]:hover {
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

/* High Contrast & Compact Box Sizing for all Inputs & Selectboxes */
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
    min-height: 38px !important;
    height: 38px !important;
}
div[data-baseweb="input"] input {
    color: #0F172A !important;
    background-color: #FFFFFF !important;
    font-weight: 500 !important;
    font-size: 13.5px !important;
    padding: 6px 12px !important;
}

div[data-baseweb="select"] {
    background-color: #FFFFFF !important;
    border: 1.5px solid #94A3B8 !important;
    border-radius: 8px !important;
    min-height: 38px !important;
}
div[data-baseweb="select"] > div {
    min-height: 36px !important;
    height: 36px !important;
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}
div[data-baseweb="select"] * {
    color: #0F172A !important;
    background-color: #FFFFFF !important;
    font-weight: 600 !important;
    font-size: 13px !important;
}
div[data-baseweb="select"] span {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}

/* Mobile Responsiveness & Form Box Adaptivity */
@media (max-width: 768px) {
    div[data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 10px !important;
    }

    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        flex: 1 1 100% !important;
        min-width: 100% !important;
        width: 100% !important;
    }

    .telkom-header-container {
        flex-direction: column !important;
        align-items: flex-start !important;
        padding: 12px 14px !important;
        gap: 8px !important;
        margin-bottom: 16px !important;
    }

    .telkom-header-brand {
        gap: 10px !important;
    }

    .telkom-header-logo {
        height: 32px !important;
    }

    .telkom-header-title {
        font-size: 15px !important;
        line-height: 1.3 !important;
    }

    .telkom-header-badge {
        font-size: 11px !important;
        padding: 3px 8px !important;
    }

    div[data-baseweb="input"],
    div[data-baseweb="select"] > div {
        min-height: 36px !important;
        height: 36px !important;
    }

    div[data-baseweb="input"] input {
        font-size: 13px !important;
        padding: 4px 8px !important;
    }

    div[data-baseweb="select"] * {
        font-size: 12px !important;
    }

    .block-container {
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
        padding-top: 1.2rem !important;
    }
}

/* Dropdown popover menu - Langsung menampilkan seluruh opsi fakultas ketika dibuka */
div[data-baseweb="popover"] ul,
div[data-baseweb="menu"] {
    max-height: 480px !important;
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
    padding: 10px 14px !important;
    font-size: 13.5px !important;
    font-weight: 500 !important;
    white-space: normal !important;
    word-break: break-word !important;
}

div[data-baseweb="popover"] li:hover,
div[data-baseweb="menu"] li:hover,
div[data-baseweb="menu"] li[aria-selected="true"] {
    background-color: #FFF1F2 !important;
    color: #BA0C2F !important;
    font-weight: 600 !important;
}

/* Sembunyikan teks instruksi panjang dan berbelit pada uploader */
div[data-testid="stFileUploaderDropzoneInstructions"],
section[data-testid="stFileUploaderDropzone"] small,
div[data-testid="stFileUploader"] small {
    display: none !important;
}

/* File Uploader Container & Dropzone: Background Putih/Cerah & Semua Teks Hitam, ringkas */
div[data-testid="stFileUploader"],
section[data-testid="stFileUploaderDropzone"],
div[data-testid="stFileUploaderDropzone"],
div[data-testid="stFileDropzone"],
div[data-testid="stFileUploaderDropzoneInstructions"] {
    background-color: #FFFFFF !important;
    background: #FFFFFF !important;
    border: 1.5px dashed #94A3B8 !important;
    border-radius: 8px !important;
}
section[data-testid="stFileUploaderDropzone"] {
    padding: 10px 14px !important;
    min-height: 0 !important;
}
div[data-testid="stFileUploader"]:hover,
section[data-testid="stFileUploaderDropzone"]:hover {
    border-color: #0F172A !important;
    background-color: #F8FAFC !important;
    background: #F8FAFC !important;
}

/* Seluruh Teks di Kolom Unggah & Dropzone Hitam Pekat */
div[data-testid="stFileUploader"] *,
section[data-testid="stFileUploaderDropzone"] * {
    color: #0F172A !important;
}
div[data-testid="stFileUploader"] small,
div[data-testid="stFileUploader"] span,
div[data-testid="stFileUploader"] p,
div[data-testid="stFileUploader"] label,
div[data-testid="stFileUploader"] div,
section[data-testid="stFileUploaderDropzone"] span,
section[data-testid="stFileUploaderDropzone"] small,
section[data-testid="stFileUploaderDropzone"] p {
    color: #0F172A !important;
    font-weight: 600 !important;
}

/* Tombol Upload (Browse files) Warna Cerah dengan Tulisan Hitam */
section[data-testid="stFileUploaderDropzone"] button,
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
section[data-testid="stFileUploaderDropzone"] button:hover,
div[data-testid="stFileUploader"] button:hover,
button[data-testid="baseButton-secondary"]:hover {
    background-color: #E2E8F0 !important;
    background: #E2E8F0 !important;
    border-color: #0F172A !important;
    color: #000000 !important;
}
section[data-testid="stFileUploaderDropzone"] button *,
div[data-testid="stFileUploader"] button *,
button[data-testid="baseButton-secondary"] * {
    color: #0F172A !important;
    font-weight: 700 !important;
}

/* ==============================================================================
   RESPONSIVE MOBILE DESIGN SYSTEM (< 768px & < 480px)
   ============================================================================== */
@media (max-width: 768px) {
    /* Responsive Container Margins */
    .block-container {
        padding-top: 0.75rem !important;
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
        padding-bottom: 2rem !important;
        max-width: 100% !important;
    }

    /* Stack and Reflow Header for Mobile */
    .telkom-header-container {
        flex-direction: column !important;
        align-items: flex-start !important;
        gap: 12px !important;
        padding: 14px 16px !important;
        border-radius: 10px !important;
        margin-bottom: 16px !important;
    }
    .telkom-header-brand {
        gap: 12px !important;
        width: 100% !important;
        align-items: center !important;
    }
    .telkom-header-logo {
        height: 38px !important;
        width: auto !important;
    }
    .telkom-header-unit {
        font-size: 10px !important;
        letter-spacing: 0.05em !important;
    }
    .telkom-header-title {
        font-size: 16px !important;
        line-height: 1.25 !important;
    }
    .telkom-header-desc {
        font-size: 11px !important;
    }
    .telkom-header-badge {
        font-size: 11px !important;
        padding: 4px 10px !important;
        border-radius: 6px !important;
        width: fit-content !important;
    }

    /* Mobile Typography & Headings */
    h1 { font-size: 20px !important; line-height: 1.3 !important; }
    h2 { font-size: 18px !important; line-height: 1.3 !important; }
    h3 { font-size: 16px !important; line-height: 1.3 !important; }
    h4 { font-size: 14px !important; line-height: 1.3 !important; }

    /* Touch-Friendly Buttons (Thumb-Optimized min 46px height) */
    button, 
    button[kind="primary"], 
    button[kind="secondary"],
    button[data-testid="baseButton-secondary"],
    div[data-testid="stFileUploader"] button {
        min-height: 46px !important;
        font-size: 14px !important;
        padding: 10px 16px !important;
        border-radius: 8px !important;
    }

    /* Prevent iOS Safari 16px auto-zoom */
    input, textarea, select, div[data-baseweb="input"] input {
        font-size: 16px !important;
        min-height: 44px !important;
    }

    /* Mobile Dropzone & Upload Box */
    div[data-testid="stFileUploader"],
    section[data-testid="stFileUploaderDropzone"] {
        padding: 14px 10px !important;
        border-radius: 8px !important;
    }
    section[data-testid="stFileUploaderDropzone"] button {
        width: 100% !important;
        margin-top: 10px !important;
        justify-content: center !important;
    }

    /* Responsive Cards */
    .telkom-card {
        padding: 14px 14px !important;
        border-radius: 10px !important;
        margin-bottom: 14px !important;
    }

    /* Full-width Columns on Mobile Screens */
    div[data-testid="stColumn"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 100% !important;
        margin-bottom: 8px !important;
    }

    /* Smooth Table Scrolling on Mobile */
    div[data-testid="stDataFrame"] {
        width: 100% !important;
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch !important;
    }

    /* Toast Notification Mobile Positioning */
    div[data-testid="stToast"] {
        width: 90% !important;
        max-width: 360px !important;
        margin: 0 auto !important;
    }
}

@media (max-width: 480px) {
    .telkom-header-brand {
        flex-direction: row !important;
        align-items: center !important;
    }
    .telkom-header-logo {
        height: 32px !important;
    }
    .telkom-header-title {
        font-size: 15px !important;
    }
    .block-container {
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }
}

/* ==============================================================================
   UPLOAD WIZARD: STEP INDICATOR
   ============================================================================== */
.ljk-stepper {
    display: flex;
    gap: 8px;
    margin-bottom: 14px;
}
.ljk-step {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    background: #FFFFFF;
    border: 1.5px solid #E2E8F0;
    border-radius: 8px;
    padding: 8px 12px;
    transition: all 0.2s ease;
}
.ljk-step-num {
    flex-shrink: 0;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 800;
    background: #F1F5F9;
    color: #64748B;
}
.ljk-step-text {
    display: flex;
    flex-direction: column;
    min-width: 0;
}
.ljk-step-title {
    font-size: 13.5px;
    font-weight: 700;
    color: #64748B;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.ljk-step-sub {
    font-size: 11px;
    color: #94A3B8;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.ljk-step.active {
    background: #FFF1F2;
    border-color: #BA0C2F;
}
.ljk-step.active .ljk-step-num {
    background: #BA0C2F;
    color: #FFFFFF;
}
.ljk-step.active .ljk-step-title {
    color: #BA0C2F;
}
.ljk-step.active .ljk-step-sub {
    color: #BA0C2F;
    opacity: 0.75;
}
.ljk-step.done .ljk-step-num {
    background: #16A34A;
    color: #FFFFFF;
}
.ljk-step.done .ljk-step-title {
    color: #16A34A;
}
/* stepper mobile handled by the override block below */

/* Stepper compact horizontal even on mobile */
.ljk-stepper {
    display: flex !important;
    flex-direction: row !important;
    gap: 6px !important;
}
@media (max-width: 768px) {
    .ljk-stepper {
        flex-direction: row !important;
        flex-wrap: nowrap !important;
    }
    .ljk-step { padding: 7px 8px !important; }
    .ljk-step-num { width: 22px !important; height: 22px !important; font-size: 11px !important; }
    .ljk-step-title { font-size: 11.5px !important; }
}

/* Progress banner shown once a stage completes */
.ljk-progress-banner {
    background: #F0FDF4;
    border: 1.5px solid #BBF7D0;
    border-radius: 10px;
    padding: 9px 14px;
    margin: 6px 0 10px 0;
}
.ljk-progress-banner-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
}
.ljk-progress-banner-label {
    font-size: 13px;
    font-weight: 700;
    color: #15803D;
}
.ljk-progress-banner-pct {
    font-size: 13px;
    font-weight: 800;
    color: #15803D;
}
.ljk-progress-track {
    width: 100%;
    height: 6px;
    border-radius: 999px;
    background: #DCFCE7;
    overflow: hidden;
}
.ljk-progress-fill {
    height: 100%;
    background: #16A34A;
    border-radius: 999px;
}

/* Force specific rows to stay side-by-side even on mobile, overriding the
   general "stack columns full-width" mobile rule below. Applied via
   st.container(key="ljk_row_...") so the class genuinely wraps its columns. */
div[class*="st-key-ljk_row_"] div[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 6px !important;
}
div[class*="st-key-ljk_row_"] div[data-testid="stColumn"] {
    min-width: 0 !important;
    width: auto !important;
    flex: 1 1 0 !important;
}

/* Per-document table: always horizontal scroll, never stacks. */
div[class*="st-key-ljk_table_scroll"] {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    padding-bottom: 4px;
}
div[class*="st-key-ljk_table_scroll"] div[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    min-width: 580px;
    gap: 4px !important;
}
div[class*="st-key-ljk_table_scroll"] div[data-testid="stColumn"] {
    min-width: 0 !important;
    width: auto !important;
}

/* Faculty + dosen row: always stays in one line */
div[class*="st-key-ljk_form_row"] div[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 10px !important;
}
div[class*="st-key-ljk_form_row"] div[data-testid="stColumn"] {
    min-width: 0 !important;
    flex: 1 1 0 !important;
}

/* Stepper: always one row */
div[class*="st-key-ljk_stepper_wrap"] div[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 6px !important;
}
div[class*="st-key-ljk_stepper_wrap"] div[data-testid="stColumn"] {
    min-width: 0 !important;
    flex: 1 1 0 !important;
}

/* Submit/export action row: always one line */
div[class*="st-key-ljk_action_row"] div[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 6px !important;
}
div[class*="st-key-ljk_action_row"] div[data-testid="stColumn"] {
    min-width: 0 !important;
    flex: 1 1 0 !important;
}

/* Compact icon-only action buttons in the table */
div[class*="st-key-ljk_table_scroll"] button {
    padding: 2px 4px !important;
    min-height: 28px !important;
    height: 28px !important;
    font-size: 14px !important;
    line-height: 1 !important;
    border-radius: 6px !important;
}
div[class*="st-key-ljk_table_scroll"] div[data-testid="stButton"] {
    margin: 0 !important;
}
div[class*="st-key-ljk_table_scroll"] div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
    padding: 0 1px !important;
}

/* Status icon cell: center-align */
.ljk-status-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    padding-top: 4px;
}

@media (max-width: 480px) {
    .ljk-stat-card { padding: 8px 4px !important; }
    .ljk-stat-card .ljk-stat-label { font-size: 9.5px !important; }
    .ljk-stat-card .ljk-stat-value { font-size: 17px !important; }
    /* On very small screens, stepper text collapses */
    .ljk-step-sub { display: none !important; }
    .ljk-step { padding: 6px 8px !important; }
    .ljk-step-title { font-size: 11px !important; }
}
</style>
""", unsafe_allow_html=True)


def render_ljk_stepper(current_step):
    """Compact horizontal stepper — always stays in one row."""
    steps = [
        ("1", "Unggah LJK", "Pilih fakultas & berkas"),
        ("2", "Tinjau Hasil", "Cek identitas & jawaban"),
        ("3", "Submit & Ekspor", "Kirim ke Google Sheet"),
    ]
    html = ['<div class="ljk-stepper">']
    for i, (num, title, sub) in enumerate(steps, start=1):
        state = "done" if i < current_step else ("active" if i == current_step else "")
        icon = "✓" if state == "done" else num
        html.append(
            f'<div class="ljk-step {state}">'
            f'<div class="ljk-step-num">{icon}</div>'
            f'<div class="ljk-step-text">'
            f'<div class="ljk-step-title">{title}</div>'
            f'<div class="ljk-step-sub">{sub}</div>'
            f'</div></div>'
        )
    html.append('</div>')
    st.markdown("".join(html), unsafe_allow_html=True)


def render_progress_banner(label, count=None, total=None):
    pct = 100 if total is None or total == 0 else round((count / total) * 100)
    count_txt = f" ({count})" if count is not None else ""
    st.markdown(
        '<div class="ljk-progress-banner">'
        '<div class="ljk-progress-banner-top">'
        f'<span class="ljk-progress-banner-label">✓ {label}{count_txt}</span>'
        f'<span class="ljk-progress-banner-pct">{pct}%</span>'
        '</div>'
        f'<div class="ljk-progress-track"><div class="ljk-progress-fill" style="width:{pct}%;"></div></div>'
        '</div>',
        unsafe_allow_html=True
    )


def classify_scan_status(preview_item, validated):
    """Classifies a scanned sheet as Gagal (alignment failed, needs a new photo),
    Perlu Validasi (scanned fine, awaiting human confirmation), or OK (validated).
    Never derived from the grade."""
    status_str = (preview_item or {}).get("status", "") if isinstance(preview_item, dict) else ""
    if "DETECTED" not in status_str:
        return "Gagal"
    return "OK" if validated else "Perlu Validasi"


def render_status_icon(label):
    """Compact icon-only status indicator — saves column space."""
    palette = {
        "OK":            ("#15803D", "✔", "Tervalidasi (OK)"),
        "Perlu Validasi":("#B45309", "⚠", "Perlu Validasi"),
        "Gagal":         ("#B91C1C", "✕", "Gagal — ganti foto"),
    }
    color, icon, title = palette.get(label, ("#475569", "•", label))
    st.markdown(
        f'<div class="ljk-status-icon">'
        f'<span title="{title}" style="color:{color}; font-size:17px; font-weight:900; line-height:1;">{icon}</span>'
        f'</div>',
        unsafe_allow_html=True
    )


def render_status_pill(label):
    palette = {
        "OK": ("#F0FDF4", "#15803D", "#BBF7D0", "✓"),
        "Perlu Validasi": ("#FFFBEB", "#B45309", "#FDE68A", "⚠"),
        "Gagal": ("#FEF2F2", "#B91C1C", "#FECACA", "✕"),
    }
    bg, fg, border, icon = palette.get(label, ("#F1F5F9", "#475569", "#E2E8F0", "•"))
    st.markdown(
        f'<span style="display:inline-flex; align-items:center; gap:4px; background:{bg}; color:{fg}; '
        f'border:1px solid {border}; border-radius:999px; padding:3px 10px; font-size:11.5px; font-weight:700; white-space:nowrap;">'
        f'{icon} {label}</span>',
        unsafe_allow_html=True
    )


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


def is_valid_phone(value):
    digits = re.sub(r"[\s\-()+]", "", value or "")
    return digits.isdigit() and 9 <= len(digits) <= 15


def process_single_page(img_bgr, doc_name, template, fakultas_pilihan, nama_pengawas, k_cache, pengawas_info=None):
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
        "File": doc_name,
        "NPM": decoded_all.get("NPM", "-"),
        "Nama Mahasiswa": decoded_all.get("NAMA", "-"),
        "Fakultas": fakultas_pilihan.split(" - ")[0] if fakultas_pilihan else "-",
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
    preview = {
        "name": doc_name,
        "status": status,
        "method": method
    }
    return student_record, preview


@st.dialog("🔍 Detail Lembar Jawaban", width="large")
def show_inspect_dialog():
    # A Streamlit dialog only stays open across st.rerun() when the call site that
    # opens it is reached unconditionally on every script run (gated by session_state,
    # not by an `if button:` block) — so idx always comes from session_state, and
    # 'Next'/'Previous' just update it and rerun.
    idx = st.session_state.get("_inspect_idx")
    if idx is None:
        return

    results = st.session_state.get("dosen_results", [])
    previews = st.session_state.get("dosen_previews", [])
    validated_list = st.session_state.get("dosen_validated", [])
    if not results:
        st.error("Data tidak ditemukan.")
        return
    idx = max(0, min(idx, len(results) - 1))
    st.session_state["_inspect_idx"] = idx

    rec = results[idx]
    prev = previews[idx] if idx < len(previews) else {}
    st_status = prev.get("status", "") if isinstance(prev, dict) else ""
    is_gagal = "DETECTED" not in st_status
    is_validated = bool(validated_list[idx]) if idx < len(validated_list) else False

    # Semua kontrol dikelompokkan berdekatan di atas: navigasi, validasi, dan ganti foto.
    with st.container(key="ljk_row_dialog_nav"):
        nav_prev, nav_next, nav_val = st.columns([1, 1, 1.3])
        with nav_prev:
            if st.button("⬅️ Sebelumnya", use_container_width=True, disabled=idx <= 0, key="inspect_nav_prev"):
                st.session_state["_inspect_idx"] = idx - 1
                st.rerun()
        with nav_next:
            if st.button("Berikutnya ➡️", use_container_width=True, disabled=idx >= len(results) - 1, key="inspect_nav_next"):
                st.session_state["_inspect_idx"] = idx + 1
                st.rerun()
        with nav_val:
            if is_validated:
                if st.button("↩️ Batalkan Validasi", use_container_width=True, key=f"unvalidate_btn_{idx}"):
                    st.session_state["dosen_validated"][idx] = False
                    st.toast("Validasi dibatalkan.", icon="↩️")
                    st.rerun()
            else:
                if st.button("✅ Tandai Divalidasi", type="primary", use_container_width=True, disabled=is_gagal, key=f"validate_btn_{idx}"):
                    st.session_state["dosen_validated"][idx] = True
                    st.toast("✅ Lembar divalidasi.", icon="✅")
                    st.rerun()

    with st.container(key="ljk_row_dialog_upload"):
        up_col, btn_col = st.columns([2, 1])
        with up_col:
            new_photo = st.file_uploader(
                "Ganti Foto",
                type=["pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"],
                key=f"replace_upload_{idx}",
                label_visibility="collapsed"
            )
        with btn_col:
            do_replace = st.button("🔁 Proses Foto Baru", use_container_width=True, disabled=new_photo is None, key=f"replace_btn_{idx}")

    if do_replace and new_photo is not None:
        template = load_default_template()
        k_cache_v = st.session_state.get("kunci_jawaban_cache", {})
        fakultas_v = st.session_state.get("_fakultas_pilihan", "-")
        pengawas_v = st.session_state.get("_nama_pengawas", "-")
        with st.spinner("Memproses foto baru..."):
            try:
                first_page = next(iter_images_from_file(new_photo, target_dpi=200, max_side=SCAN_MAX_SIDE), None)
                if first_page and template:
                    doc_name, img_bgr = first_page
                    new_rec, new_prev = process_single_page(img_bgr, doc_name, template, fakultas_v, pengawas_v, k_cache_v, st.session_state.get("_pengawas_info"))
                    st.session_state["dosen_results"][idx] = new_rec
                    st.session_state["dosen_previews"][idx] = new_prev
                    st.session_state["dosen_validated"][idx] = False
                    st.toast("✅ Foto berhasil diganti & diproses ulang!", icon="🔁")
                    st.rerun()
            except Exception as e:
                st.error(f"Gagal memproses foto baru: {str(e)}")

    st.markdown(
        f"<div style='font-size:12.5px; color:#64748B; margin-top:6px;'>Lembar {idx + 1} / {len(results)}</div>"
        f"<div><b>{rec.get('Nama Mahasiswa', '-')}</b> &bull; NPM <code>{rec.get('NPM', '-')}</code> &bull; "
        f"{rec.get('Fakultas (LJK)', rec.get('Fakultas', '-'))} &bull; Kode Soal {rec.get('Kode Soal', '-')} "
        f"&bull; Jawaban Terisi {rec.get('Jawaban Terisi', '-')}</div>"
        f"<span style='color:#64748B; font-size:12.5px;'>Berkas: {rec.get('File', '-')}</span>",
        unsafe_allow_html=True
    )
    if is_gagal:
        st.warning(f"⚠️ Ujung pojok LJK tidak terdeteksi (**{st_status}**) — ganti dengan foto baru di atas.")

# ------------------------------------------------------------------------------
# TOP TELKOM BRAND HEADER
# ------------------------------------------------------------------------------
logo_b64 = get_telkom_logo_b64()
st.markdown(f"""
<div class="telkom-header-container">
    <div class="telkom-header-brand">
        <img src="{logo_b64}" alt="Telkom University" class="telkom-header-logo"/>
        <div class="telkom-header-text">
            <span class="telkom-header-title">Evaluasi LJK Profiling Literasi Numerik</span>
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

# Auto-load template by default on dashboard, refreshing automatically if updated on disk
tpl_file_path = get_default_template_path()
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

# Google Sheets URL permanen (Master Nilai & Kunci Jawaban)
TARGET_GSHEET_URL = "https://docs.google.com/spreadsheets/d/1vRpXz-w55XtX33WAx6b677yQXoM3oZ8jcQ_26m1XEYo/edit?gid=1945243931#gid=1945243931"

def render_sidebar_footer():
    st.sidebar.markdown("---")
    st.sidebar.link_button(
        "🌐 View Google Sheet (Admin)",
        TARGET_GSHEET_URL,
        use_container_width=True,
        help="Buka Google Sheet master (Sheet1 & Sheet Kunci Jawaban)"
    )
    st.sidebar.markdown("""
    <div style="margin-top: 28px; padding-top: 14px; border-top: 1px solid #E2E8F0; text-align: center;">
        <span style="font-size: 11px; color: #64748B; font-weight: 500;">
            developed by <strong style="color: #0F172A;">WHS</strong>
        </span>
    </div>
    """, unsafe_allow_html=True)

# High-contrast, clean sidebar with ONLY logo
st.sidebar.markdown(f"""
<div style="display: flex; justify-content: center; align-items: center; padding: 6px 0 14px 0; margin-bottom: 12px; border-bottom: 1px solid #E2E8F0;">
    <img src="{logo_b64}" style="height: 48px; width: auto; object-fit: contain;"/>
</div>
""", unsafe_allow_html=True)

mode = st.sidebar.radio(
    "Pilih Menu:",
    [
        "Portal Evaluasi LJK",
        "Kalibrasi LJK"
    ],
    index=0
)

sub_mode = "Editor Template"
if mode == "Kalibrasi LJK":
    sub_mode = st.sidebar.radio(
        "Sub Menu:",
        [
            "Editor Template",
            "OMR Reader"
        ],
        index=0
    )

    # Quick badge for default template in sidebar
    active_tpl_name = os.path.basename(get_default_template_path())
    st.sidebar.markdown(f"""
    <div style="background-color: #FFF1F2; border: 1px solid #FECDD3; border-radius: 8px; padding: 10px 12px; margin-top: 14px; margin-bottom: 12px;">
        <div style="font-size: 11px; font-weight: 700; color: #BA0C2F; text-transform: uppercase;">⭐ Template Default Aktif</div>
        <div style="font-size: 12px; font-weight: 700; color: #0F172A; margin-top: 2px;">templates/{active_tpl_name}</div>
        <div style="font-size: 11px; color: #334155; margin-top: 2px;">12 Field Lengkap &bull; 75 Soal Ujian Resmi</div>
    </div>
    """, unsafe_allow_html=True)

    if st.sidebar.button(f"🔄 Muat Ulang {active_tpl_name}", use_container_width=True, help="Kembalikan konfigurasi field ke template bawaan"):
        default_tpl = load_default_template()
        if default_tpl and "fields" in default_tpl:
            st.session_state["calibrated_fields"] = default_tpl["fields"]
            st.session_state["template_metadata"] = default_tpl.get("canvas", {"width": 1700, "height": 2400})
            st.session_state["chosen_bubble_shape"] = default_tpl.get("bubble_shape", "square")
            st.session_state["editing_field_name"] = list(default_tpl["fields"].keys())[0]
            for k in list(st.session_state.keys()):
                if k.startswith("active_box_") or k.startswith("roi_editor_"):
                    del st.session_state[k]
            st.toast(f"✅ {active_tpl_name} berhasil dimuat ulang!", icon="⭐")
            st.rerun()
else:
    active_tpl_name = os.path.basename(get_default_template_path())

# ==============================================================================
# STRUKTUR KOLOM REKAPITULASI (DATE -> PENGAWAS -> MAHASISWA -> NILAI -> JAWABAN)
# ==============================================================================
ORDERED_REKAP_PREFIX = [
    "Submit Date",
    "Nama Pengawas",
    "No HP Pengawas",
    "Ruangan",
    "File",
    "NPM",
    "Nama Mahasiswa",
    "Fakultas",
    "Program Studi",
    "Fakultas (LJK)",
    "Kode Soal",
    "Jawaban Terisi",
    "Nilai",
    "Jumlah Benar",
    "Jumlah Salah",
    "Jumlah Kosong",
    "Kunci Terpakai",
]

def reorder_rekap_columns(df):
    rename_dict = {}
    if "Pengawas / Dosen" in df.columns and "Nama Pengawas" not in df.columns:
        rename_dict["Pengawas / Dosen"] = "Nama Pengawas"
    if "Fakultas Mahasiswa" in df.columns and "Fakultas" not in df.columns:
        rename_dict["Fakultas Mahasiswa"] = "Fakultas"
    if "Fakultas (Pengawas)" in df.columns and "Fakultas" not in df.columns:
        rename_dict["Fakultas (Pengawas)"] = "Fakultas"
    if rename_dict:
        df = df.rename(columns=rename_dict)

    drop_cols = [c for c in ["Status LJK", "Persentase Terisi"] if c in df.columns]
    if drop_cols:
        df = df.drop(columns=drop_cols)

    cols = list(df.columns)
    first_cols = [c for c in ORDERED_REKAP_PREFIX if c in cols]
    kuis_cols = sorted([c for c in cols if (c.lower().startswith("q") and len(c) <= 5) or "kuis" in c.lower()])
    soal_cols = sorted([c for c in cols if c.startswith("soal_")])
    other_cols = [c for c in cols if c not in first_cols and c not in kuis_cols and c not in soal_cols]
    return df[first_cols + kuis_cols + soal_cols + other_cols]

# ==============================================================================
# MODE UTAMA: PORTAL EVALUASI LJK
# ==============================================================================
if mode == "Portal Evaluasi LJK":
    render_sidebar_footer()

    _has_results = bool(st.session_state.get("dosen_results"))
    _is_submitted = bool(st.session_state.get("dosen_submitted"))
    _current_step = 3 if _is_submitted else (2 if _has_results else 1)
    render_ljk_stepper(_current_step)

    # 1. Identitas Pengawas & Fakultas / Program Studi Mahasiswa
    with st.container(key="ljk_form_row_top"):
        col_dos, col_hp, col_room = st.columns([3, 2, 2])
        with col_dos:
            nama_pengawas = st.text_input(
                "Nama Lengkap Pengawas",
                value="",
                placeholder="Nama lengkap pengawas...",
                help="Wajib diisi: Nama lengkap pengawas."
            )
        with col_hp:
            hp_pengawas = st.text_input(
                "Nomor HP Pengawas",
                value="",
                placeholder="08xxxxxxxxxx",
                help="Wajib diisi: Nomor HP pengawas yang dapat dihubungi."
            )
        with col_room:
            ruangan = st.text_input(
                "Ruangan",
                value="",
                placeholder="Contoh: TULT 0603",
                help="Wajib diisi: Ruangan pelaksanaan."
            )
        col_fak, col_prodi = st.columns([1, 1])
        with col_fak:
            fakultas_pilihan = st.selectbox(
                "Fakultas Mahasiswa",
                options=list(FAKULTAS_PRODI.keys()),
                index=None,
                placeholder="-- Pilih Fakultas --",
                help="Wajib dipilih: Fakultas mahasiswa yang dievaluasi."
            )
        with col_prodi:
            prodi_pilihan = st.selectbox(
                "Program Studi Mahasiswa",
                options=FAKULTAS_PRODI.get(fakultas_pilihan, []),
                index=None,
                placeholder="-- Pilih Program Studi --" if fakultas_pilihan else "-- Pilih Fakultas dulu --",
                disabled=not fakultas_pilihan,
                help="Wajib dipilih: Program studi mahasiswa (mengikuti fakultas)."
            )
    pengawas_info = {
        "hp": hp_pengawas.strip(),
        "ruangan": ruangan.strip(),
        "prodi": prodi_pilihan or "-",
    }
    st.session_state["_fakultas_pilihan"] = fakultas_pilihan
    st.session_state["_nama_pengawas"] = nama_pengawas
    st.session_state["_pengawas_info"] = pengawas_info

    # Status Kunci Jawaban Auto-Nilai (Dikelola oleh Admin di Google Sheet)
    if "kunci_jawaban_cache" not in st.session_state or st.session_state["kunci_jawaban_cache"] is None:
        try:
            conn_kj = st.connection("gsheets", type=GSheetsConnection)
            st.session_state["kunci_jawaban_cache"] = load_kunci_jawaban_from_gsheet(TARGET_GSHEET_URL, conn=conn_kj)
        except Exception:
            st.session_state["kunci_jawaban_cache"] = {}

    k_cache = st.session_state.get("kunci_jawaban_cache", {})

    # 2. Area Unggah Berkas LJK — satu kotak native saja, pemindaian berjalan otomatis
    uploaded_files_dosen = st.file_uploader(
        "📤 Unggah LJK (otomatis dipindai)",
        type=["pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"],
        accept_multiple_files=True,
        key="ljk_uploader",
        help="PDF, JPG, PNG, HEIC/HEIF, WEBP — mendukung multi-file & multi-halaman PDF. Maks. 10MB per berkas."
    )

    # Bersihkan hasil evaluasi sebelumnya ketika file baru di-upload
    _upload_key = tuple(sorted(
        (getattr(uf, "name", ""), getattr(uf, "size", 0))
        for uf in (uploaded_files_dosen or [])
    ))
    if _upload_key != st.session_state.get("_last_upload_key"):
        st.session_state["_last_upload_key"] = _upload_key
        if _upload_key and "dosen_results" in st.session_state:
            del st.session_state["dosen_results"]
        if _upload_key and "dosen_previews" in st.session_state:
            del st.session_state["dosen_previews"]
        st.session_state.pop("dosen_validated", None)
        st.session_state["dosen_submitted"] = False
        st.session_state.pop("_preview_idx", None)
        st.session_state.pop("_inspect_idx", None)
        st.session_state.pop("_auto_scanned_key", None)

    # Validasi Berkas & Form (Batas 10MB per berkas)
    MAX_FILE_SIZE_MB = 10
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
    oversized_files = [uf.name for uf in (uploaded_files_dosen or []) if getattr(uf, "size", 0) > MAX_FILE_SIZE_BYTES]

    _phone_ok = is_valid_phone(hp_pengawas)
    is_form_complete = (
        bool(nama_pengawas.strip()) and _phone_ok and bool(ruangan.strip())
        and bool(fakultas_pilihan) and bool(prodi_pilihan)
    )
    has_files = bool(uploaded_files_dosen) and len(oversized_files) == 0

    # Pemindaian berjalan otomatis begitu berkas & form lengkap.
    already_scanned = has_files and st.session_state.get("_auto_scanned_key") == _upload_key
    should_auto_scan = has_files and is_form_complete and not already_scanned

    if oversized_files:
        st.error(f"⚠️ Berkas melebihi batas ukuran 10MB: **{', '.join(oversized_files)}**.")
    elif bool(uploaded_files_dosen) and not is_form_complete:
        missing_fields = []
        if not nama_pengawas.strip():
            missing_fields.append("Nama Lengkap Pengawas")
        if not hp_pengawas.strip():
            missing_fields.append("Nomor HP Pengawas")
        elif not _phone_ok:
            missing_fields.append("Nomor HP Pengawas (format tidak valid)")
        if not ruangan.strip():
            missing_fields.append("Ruangan")
        if not fakultas_pilihan:
            missing_fields.append("Fakultas Mahasiswa")
        if not prodi_pilihan:
            missing_fields.append("Program Studi Mahasiswa")
        st.warning(f"⚠️ Wajib diisi: **{' & '.join(missing_fields)}** sebelum evaluasi.")

    if uploaded_files_dosen and should_auto_scan and is_form_complete:
        template = load_default_template()
        if not template:
            st.error("Template resmi tidak ditemukan di folder templates/ maupun direktori aplikasi.")
            st.stop()

        # Pastikan kunci jawaban terbaru tersinkronisasi dari Google Sheet sebelum penilaian
        try:
            conn_kj = st.connection("gsheets", type=GSheetsConnection)
            st.session_state["kunci_jawaban_cache"] = load_kunci_jawaban_from_gsheet(TARGET_GSHEET_URL, conn=conn_kj)
        except Exception:
            pass
        k_cache = st.session_state.get("kunci_jawaban_cache", {})

        # Streaming: decode -> pindai -> buang, satu halaman per iterasi (RAM ~1 gambar).
        n_files = len(uploaded_files_dosen)
        prog = st.progress(0, text=f"Memindai berkas 0 / {n_files}...")
        dosen_results = []
        dosen_previews = []

        for f_idx, uf in enumerate(uploaded_files_dosen):
            try:
                for doc_name, img_bgr in iter_images_from_file(uf, target_dpi=200, max_side=SCAN_MAX_SIDE):
                    student_record, preview = process_single_page(img_bgr, doc_name, template, fakultas_pilihan, nama_pengawas, k_cache, pengawas_info)
                    del img_bgr
                    dosen_results.append(student_record)
                    dosen_previews.append(preview)
                    prog.progress(f_idx / n_files, text=f"Memindai berkas {f_idx + 1} / {n_files} — {len(dosen_results)} lembar terbaca...")
            except Exception as e:
                st.error(f"Error memproses berkas {getattr(uf, 'name', 'LJK')}: {str(e)}")
            prog.progress((f_idx + 1) / n_files, text=f"Memindai berkas {f_idx + 1} / {n_files} — {len(dosen_results)} lembar terbaca...")

        st.session_state["dosen_results"] = dosen_results
        st.session_state["dosen_previews"] = dosen_previews
        st.session_state["dosen_validated"] = [False] * len(dosen_results)
        st.session_state["dosen_submitted"] = False
        st.session_state["_auto_scanned_key"] = _upload_key
        st.toast(f"✅ Selesai memindai {len(dosen_results)} lembar LJK!", icon="🔍")
        st.rerun()

    # Show results if available (Tahap 2: Tinjau & Submit)
    if "dosen_results" in st.session_state and st.session_state["dosen_results"]:
        results = st.session_state["dosen_results"]
        previews_list = st.session_state.get("dosen_previews", [])
        if len(st.session_state.get("dosen_validated", [])) != len(results):
            st.session_state["dosen_validated"] = [False] * len(results)
        validated_list = st.session_state["dosen_validated"]

        is_submitted = st.session_state.get("dosen_submitted", False)

        status_labels = [
            classify_scan_status(previews_list[i] if i < len(previews_list) else {}, validated_list[i])
            for i in range(len(results))
        ]
        n_ok = status_labels.count("OK")
        all_ok = len(results) > 0 and n_ok == len(results)

        render_progress_banner("Tahap 1 Selesai: LJK Berhasil Dipindai", count=len(results))
        if is_submitted:
            render_progress_banner("Tahap 2 Selesai: Submit Berhasil", count=len(results))
            st.success("✅ **Data LJK Berhasil Disubmit ke Google Sheet!**")
        elif all_ok:
            st.info("💡 Semua lembar sudah divalidasi — klik **Submit Hasil LJK ke Google Sheet** di bawah.")
        else:
            st.info(f"💡 **{len(results) - n_ok} dari {len(results)}** lembar masih perlu divalidasi. Periksa identitas (nama, NPM, kode soal) dan keterisian jawaban di tiap baris, lalu klik ✅ (🔍 untuk ganti foto bila gagal), atau **Validasi Semua**.")

        df_full = pd.DataFrame(results)
        df_full = reorder_rekap_columns(df_full)
        for c in df_full.columns:
            df_full[c] = df_full[c].astype(str)

        # Ringkasan Statistik (non-nilai): penilaian hanya dihitung di background untuk Google Sheet.
        # Dibungkus st.container(key=...) supaya 3 kartu ini tetap satu baris di layar mobile.
        with st.container(key="ljk_row_stats"):
            stat_cols = st.columns(3)
            for col, label, value, color in [
                (stat_cols[0], "Dokumen Diupload", len(uploaded_files_dosen or []), "#0F172A"),
                (stat_cols[1], "LJK Terbaca", len(results), "#0F172A"),
                (stat_cols[2], "Status OK", n_ok, "#15803D"),
            ]:
                with col:
                    st.markdown(
                        '<div class="telkom-card ljk-stat-card" style="text-align:center; padding:10px 8px !important; margin-bottom:8px !important;">'
                        f'<div class="ljk-stat-label" style="font-size:11px; color:#64748B; font-weight:600; white-space:nowrap;">{label}</div>'
                        f'<div class="ljk-stat-value" style="font-size:22px; font-weight:800; color:{color};">{value}</div>'
                        '</div>',
                        unsafe_allow_html=True
                    )

        st.markdown("<div style='font-size:14.5px; font-weight:700; margin:4px 0 6px 0;'>📋 Daftar Mahasiswa & Hasil Pindaian</div>", unsafe_allow_html=True)
        if not all_ok or n_ok > 0:
            with st.container(key="ljk_row_validasi"):
                hdr_col1, hdr_col2 = st.columns(2)
                with hdr_col1:
                    if not all_ok:
                        if st.button("✅ Validasi Semua", use_container_width=True, help="Tandai semua lembar yang berhasil terbaca sebagai tervalidasi"):
                            for i in range(len(results)):
                                if status_labels[i] != "Gagal":
                                    st.session_state["dosen_validated"][i] = True
                            st.rerun()
                with hdr_col2:
                    if n_ok > 0:
                        if st.button("↩️ Un-validasi Semua", use_container_width=True, help="Batalkan validasi semua lembar"):
                            st.session_state["dosen_validated"] = [False] * len(results)
                            st.rerun()

        # Compact table: status icon | nama+file | NPM | fak | kode | terisi | aksi(3 btns)
        table_widths = [0.4, 2.2, 1.2, 0.7, 0.6, 0.8, 0.95]
        _idx_to_delete = None
        with st.container(key="ljk_table_scroll"):
            header_cols = st.columns(table_widths)
            for hc, lbl in zip(header_cols, ["", "Nama / Berkas", "NPM", "Fakultas", "Kode", "Terisi", "Aksi"]):
                hc.markdown(
                    f"<span style='font-size:10.5px; font-weight:700; color:#64748B; "
                    f"text-transform:uppercase; white-space:nowrap; letter-spacing:.04em;'>{lbl}</span>",
                    unsafe_allow_html=True
                )
            st.markdown('<hr style="margin:2px 0 3px 0; border-color:#E2E8F0;">', unsafe_allow_html=True)

            for i, rec in enumerate(results):
                row_cols = st.columns(table_widths)

                # Col 0 — status icon only
                with row_cols[0]:
                    render_status_icon(status_labels[i])

                # Col 1 — nama mahasiswa + filename
                with row_cols[1]:
                    st.markdown(
                        f"<div style='font-weight:700;color:#0F172A;font-size:12.5px;"
                        f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                        f"max-width:100%;'>{rec.get('Nama Mahasiswa','-')}</div>"
                        f"<div style='font-size:10px;color:#94A3B8;overflow:hidden;"
                        f"text-overflow:ellipsis;white-space:nowrap;max-width:100%;'>"
                        f"{rec.get('File','-')}</div>",
                        unsafe_allow_html=True
                    )

                # Col 2 — NPM
                with row_cols[2]:
                    st.markdown(
                        f"<div style='font-size:12px;padding-top:3px;"
                        f"white-space:nowrap;font-family:monospace;'>{rec.get('NPM','-')}</div>",
                        unsafe_allow_html=True
                    )

                # Col 3 — Fakultas (abbrev only)
                with row_cols[3]:
                    fak_abbr = rec.get("Fakultas", "-").split(" - ")[0] if " - " in rec.get("Fakultas", "") else rec.get("Fakultas", "-")
                    st.markdown(
                        f"<div style='font-size:11.5px;padding-top:3px;white-space:nowrap;'>{fak_abbr}</div>",
                        unsafe_allow_html=True
                    )

                # Col 4 — Kode Soal
                with row_cols[4]:
                    st.markdown(
                        f"<div style='font-size:12px;padding-top:3px;white-space:nowrap;'>{rec.get('Kode Soal','-')}</div>",
                        unsafe_allow_html=True
                    )

                # Col 5 — Jawaban Terisi
                with row_cols[5]:
                    st.markdown(
                        f"<div style='font-size:12px;padding-top:3px;white-space:nowrap;'>{rec.get('Jawaban Terisi','-')}</div>",
                        unsafe_allow_html=True
                    )

                # Col 6 — 3 icon action buttons: Inspect | Validate/Unvalidate | Delete
                with row_cols[6]:
                    btn_c1, btn_c2, btn_c3 = st.columns(3)
                    with btn_c1:
                        if st.button("🔍", key=f"inspect_btn_{i}",
                                     use_container_width=True, help="Detail / ganti foto"):
                            st.session_state["_inspect_idx"] = i
                            st.rerun()
                    with btn_c2:
                        if status_labels[i] == "Gagal":
                            st.button("✅", key=f"quick_val_{i}",
                                      use_container_width=True, disabled=True,
                                      help="Ganti foto dulu")
                        elif status_labels[i] == "OK":
                            if st.button("↩️", key=f"quick_unval_{i}",
                                         use_container_width=True,
                                         help="Batalkan validasi"):
                                st.session_state["dosen_validated"][i] = False
                                st.rerun()
                        else:
                            if st.button("✅", key=f"quick_val_{i}",
                                         use_container_width=True,
                                         help="Tandai divalidasi"):
                                st.session_state["dosen_validated"][i] = True
                                st.rerun()
                    with btn_c3:
                        if st.button("🗑️", key=f"delete_btn_{i}",
                                     use_container_width=True,
                                     help="Hapus lembar ini dari daftar"):
                            _idx_to_delete = i

                st.markdown('<hr style="margin:1px 0; border-color:#F1F5F9;">', unsafe_allow_html=True)

        # Handle delete outside the loop to avoid index mutation mid-iteration
        if _idx_to_delete is not None:
            st.session_state["dosen_results"].pop(_idx_to_delete)
            st.session_state["dosen_previews"].pop(_idx_to_delete)
            st.session_state["dosen_validated"].pop(_idx_to_delete)
            st.toast("🗑️ Lembar dihapus dari daftar.", icon="🗑️")
            st.rerun()

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        with st.container(key="ljk_action_row_submit"):
            col_act1, col_act2, col_act3 = st.columns([1.8, 1.2, 1.0])
            with col_act1:
                if not is_submitted:
                    if st.button(
                        "📤 Submit ke Google Sheet",
                        type="primary",
                        use_container_width=True,
                        disabled=not all_ok,
                        help="Semua lembar harus OK sebelum bisa disubmit." if not all_ok else "Transfer data ke Google Sheet"
                    ):
                        try:
                            with st.spinner("Mentransfer data ke Google Sheet..."):
                                conn = st.connection("gsheets", type=GSheetsConnection)
                                df_to_sync = df_full.copy()
                                try:
                                    existing_sheet_df = conn.read(spreadsheet=TARGET_GSHEET_URL, worksheet="Sheet1", ttl=0).dropna(how="all")
                                    if not existing_sheet_df.empty and "NPM" in existing_sheet_df.columns:
                                        combined_sheet_df = pd.concat([existing_sheet_df, df_to_sync], ignore_index=True)
                                        combined_sheet_df = reorder_rekap_columns(combined_sheet_df)
                                        conn.update(spreadsheet=TARGET_GSHEET_URL, worksheet="Sheet1", data=combined_sheet_df)
                                    else:
                                        conn.update(spreadsheet=TARGET_GSHEET_URL, worksheet="Sheet1", data=df_to_sync)
                                except Exception:
                                    conn.update(spreadsheet=TARGET_GSHEET_URL, worksheet="Sheet1", data=df_to_sync)
                            st.session_state["dosen_submitted"] = True
                            st.toast("✅ Data berhasil disubmit ke Google Sheet!", icon="📤")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Gagal transfer ke Google Sheet: {str(e)}")
                else:
                    st.button("✅ Berhasil Disubmit", disabled=True, use_container_width=True)

            with col_act2:
                st.link_button(
                    "🌐 Lihat Google Sheet",
                    TARGET_GSHEET_URL,
                    type="primary" if is_submitted else "secondary",
                    use_container_width=True,
                    help="Buka Google Sheet master"
                )

            with col_act3:
                if st.button("🔄 Evaluasi Baru", use_container_width=True, help="Reset untuk berkas baru"):
                    for k in ("dosen_results", "dosen_previews", "dosen_validated", "_inspect_idx"):
                        st.session_state.pop(k, None)
                    st.session_state["dosen_submitted"] = False
                    st.rerun()

        # Dipanggil tanpa syarat (bukan di dalam if st.button) supaya dialog tetap
        # terbuka saat tombol Sebelumnya/Berikutnya/Validasi di dalamnya memicu rerun.
        if st.session_state.get("_inspect_idx") is not None:
            show_inspect_dialog()

# ==============================================================================
# MODE 2: KALIBRASI LJK (SUB MENU: EDITOR TEMPLATE)
# ==============================================================================
elif mode == "Kalibrasi LJK" and sub_mode == "Editor Template":
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

    render_sidebar_footer()

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
        st.caption(f"Template aktif: **templates/{active_tpl_name}** ({len(st.session_state.get('calibrated_fields', {}))} Section)")
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
    sample_ljk_path = os.path.join(os.path.dirname(__file__), "templates", "sample_1.pdf")
    if not os.path.exists(sample_ljk_path):
        sample_ljk_path = os.path.join(os.path.dirname(__file__), "LJK.pdf")
    has_sample = os.path.exists(sample_ljk_path)

    if not uploaded_file:
        col_inf1, col_inf2 = st.columns([3, 1])
        with col_inf1:
            st.info("👋 Silakan upload file LJK template (PDF, JPG, atau PNG) di sidebar kiri untuk kalibrasi visual, atau gunakan LJK contoh resmi.")
        with col_inf2:
            if has_sample and st.button("📄 Muat LJK Contoh", type="primary", use_container_width=True):
                st.session_state["use_sample_ljk"] = True
                st.rerun()

        if st.session_state.get("use_sample_ljk") and has_sample:
            with open(sample_ljk_path, "rb") as f:
                uploaded_file = f.read()
                setattr(uploaded_file, "name", os.path.basename(sample_ljk_path))
        else:
            if st.session_state["calibrated_fields"]:
                st.markdown(f"#### 📦 Rincian {len(st.session_state['calibrated_fields'])} Field dari {active_tpl_name} (Default Terpilih):")
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

    # Green Frame Crop + ArUco Machine Registration (<0.05s)
    t_start = time.time()
    warped_img, ordered_pts, method_used, corner_ids, detected_dict, status, aruco_reg = detect_corners_and_crop(
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
    st.session_state["aruco_registration"] = aruco_reg

    # Collapsible Preview for Crop Geometry & Machine Registration
    with st.expander(f"📐 Penyelarasan Sudut Otomatis: {status} ({t_elapsed:.3f}s) — Klik untuk intip gambar crop", expanded=False):
        c_crop1, c_crop2 = st.columns(2)
        with c_crop1:
            regmarks_overlay = draw_regmarks_overlay(
                img_bgr, ordered_pts, method=method_used, corner_ids=corner_ids,
                status=status, crop_mode=c_mode, aruco_registration=aruco_reg
            )
            st.image(cv_to_pil(regmarks_overlay), caption="Batas Crop (Green Frame) & Registrasi Mesin (ArUco)", use_container_width=True)
        with c_crop2:
            coord_overlay = draw_cropped_coordinate_system_overlay(warped_img, aruco_registration=aruco_reg)
            st.image(cv_to_pil(coord_overlay), caption=f"Sistem Koordinat Hasil Crop ({canvas_w}×{canvas_h} px)", use_container_width=True)

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
                "crop_boundary": "green_frame",
                "aruco_dict": detected_dict,
                "aruco_corner_ids": corner_ids,
                "aruco_registration": {
                    "status": aruco_reg.get("status") if aruco_reg else "NONE",
                    "marker_ids": aruco_reg.get("marker_ids") if aruco_reg else {},
                    "normalized_centers": {k: [round(float(v[0]), 2), round(float(v[1]), 2)] for k, v in aruco_reg.get("normalized_centers", {}).items()} if aruco_reg else {},
                    "orientation_angle": aruco_reg.get("orientation_angle", 0) if aruco_reg else 0,
                } if aruco_reg else None,
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
# MODE 2: KALIBRASI LJK (SUB MENU: OMR READER)
# ==============================================================================
elif mode == "Kalibrasi LJK" and sub_mode == "OMR Reader":
    st.markdown("""
    <div style="margin-bottom: 16px;">
        <h2 style="font-size: 20px; font-weight: 700; color: #0F172A; margin: 0 0 4px 0;">📊 OMR Reader</h2>
        <p style="font-size: 13px; color: #64748B; margin: 0;">Pengujian pembacaan batch lembar jawaban dengan analisis detail deteksi optik.</p>
    </div>
    """, unsafe_allow_html=True)

    col_up1, col_up2 = st.columns([1.1, 1.9])
    with col_up1:
        st.markdown("#### 1. Template LJK")
        tpl_source = st.radio(
            "Pilih Template OMR:",
            options=[
                f"⭐ {active_tpl_name} (Default)",
                "📤 Upload Template JSON Kustom"
            ],
            index=0,
            help=f"{active_tpl_name} adalah template resmi untuk ujian Telkom University (12 Section, 75 Soal)."
        )

        template = None
        if tpl_source == f"⭐ {active_tpl_name} (Default)":
            template = load_default_template()
            if template:
                sec_list = list(template.get("fields", {}).keys())
                st.markdown(f"""
                <div style="background-color: #FFF1F2; border: 1px solid #FECDD3; border-radius: 8px; padding: 12px; margin-top: 6px;">
                    <div style="font-weight: 700; color: #BA0C2F; font-size: 13px;">⭐ Terpilih: {active_tpl_name}</div>
                    <div style="font-size: 12px; color: #334155; margin-top: 4px; line-height: 1.5;">
                        • <b>{len(sec_list)} Section Aktif</b>: {', '.join(sec_list[:5])}...<br>
                        • <b>75 Soal Pilihan Ganda</b> (Soal A s/d E)<br>
                        • Format: <b>Kotak (Square)</b> &bull; ArUco: <b>DICT_4X4_50</b>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.error("File template tidak ditemukan di direktori aplikasi.")
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
        test_pdf_path = os.path.join(os.path.dirname(__file__), "templates", "sample_1.pdf")
        if not os.path.exists(test_pdf_path):
            test_pdf_path = os.path.join(os.path.dirname(__file__), "filled_LJK.xlsx.pdf")
        if not uploaded_files and os.path.exists(test_pdf_path):
            if st.button(f"📄 Uji Coba Cepat: Muat {os.path.basename(test_pdf_path)}", use_container_width=True):
                with open(test_pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                    setattr(pdf_bytes, "name", os.path.basename(test_pdf_path))
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
    ambig_margin = st.sidebar.slider("Margin Ganda (Ambiguity Margin)", 0.03, 0.18, 0.06, 0.01,
                                     help="Selisih minimal antara opsi teratas dan opsi kedua untuk dianggap jawaban tunggal.")
    render_sidebar_footer()

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
                warped, pts, method, _, _, status, _ = detect_corners_and_crop(
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

                overlay_img = draw_reading_overlay(warped, fields_dict, gray_warped, thresh=read_thresh, margin=ambig_margin)
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
