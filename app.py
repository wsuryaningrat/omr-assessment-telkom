"""
Telkom University Math Center - Full OMR Calibration & Testing Application.
100% Deterministic OpenCV Engine (Zero OCR, Zero LLM, Zero AI Vision).
"""

import json
import os
import io
import time
from typing import Dict, List, Any, Optional

import streamlit as st
import numpy as np
import pandas as pd
import cv2
from PIL import Image

# Import OMR Engine Modules
from omr.pipeline import process_ljk
from omr.alignment import check_quality_gate, align_image, calculate_blur_score
from omr.bubbles import score_single_bubble
from utils.export import export_to_csv, export_to_excel, prepare_dataframe
from utils.visualizer import create_omr_debug_overlay
from tests.generate_synthetic import generate_student_ljk, create_blank_canonical_ljk

# ==============================================================================
# STREAMLIT APP CONFIGURATION & BRANDING CSS
# ==============================================================================
st.set_page_config(
    page_title="Math Center OMR System - Telkom University",
    page_icon="🔴",
    layout="wide",
    initial_sidebar_state="expanded"
)

CUSTOM_CSS = """
<style>
    /* Google Inter font styling */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* Telkom University Theme Colors */
    :root {
        --telkom-red: #BA0C2F;
        --telkom-red-hover: #980925;
        --telkom-red-dark: #7B061D;
        --slate-bg: #F8FAFC;
    }

    /* Main Container Padding */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 3rem;
        max-width: 1400px;
    }

    /* Header Bar */
    .telkom-header-bar {
        background-color: #BA0C2F;
        height: 4px;
        width: 100%;
        border-radius: 2px 2px 0 0;
    }

    .telkom-brand-card {
        background: white;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        margin-bottom: 1.5rem;
    }

    /* Status Badges */
    .badge-ok {
        background-color: #DCFCE7;
        color: #166534;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
        display: inline-block;
        border: 1px solid #BBF7D0;
    }
    .badge-review {
        background-color: #FEF3C7;
        color: #92400E;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
        display: inline-block;
        border: 1px solid #FDE68A;
    }
    .badge-failed {
        background-color: #FEE2E2;
        color: #991B1B;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
        display: inline-block;
        border: 1px solid #FECACA;
    }
    .badge-reviewed {
        background-color: #E0E7FF;
        color: #3730A3;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
        display: inline-block;
        border: 1px solid #C7D2FE;
    }

    /* Card Panels */
    .stat-card {
        background: white;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 1rem 1.25rem;
        text-align: center;
        box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }
    .stat-val {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0F172A;
        line-height: 1.2;
    }
    .stat-lbl {
        font-size: 0.8rem;
        color: #64748B;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 0.25rem;
    }

    /* Compact Answer Grid */
    .answer-cell {
        background: #F1F5F9;
        border: 1px solid #CBD5E1;
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 0.75rem;
        font-family: monospace;
        margin-bottom: 2px;
    }
    .answer-cell-correct {
        background: #DCFCE7;
        border: 1px solid #86EFAC;
        color: #166534;
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 0.75rem;
        font-family: monospace;
        margin-bottom: 2px;
    }
    .answer-cell-wrong {
        background: #FEE2E2;
        border: 1px solid #FCA5A5;
        color: #991B1B;
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 0.75rem;
        font-family: monospace;
        margin-bottom: 2px;
    }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ==============================================================================
# SESSION STATE INITIALIZATION
# ==============================================================================
DEFAULT_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "default_template.json")

if "template" not in st.session_state:
    try:
        with open(DEFAULT_TEMPLATE_PATH, "r") as f:
            st.session_state["template"] = json.load(f)
    except Exception:
        st.session_state["template"] = {}

if "processed_results" not in st.session_state:
    st.session_state["processed_results"] = []

if "uploaded_files_cache" not in st.session_state:
    st.session_state["uploaded_files_cache"] = {}


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================
def load_image_from_bytes(file_bytes: bytes) -> Optional[np.ndarray]:
    """Convert uploaded bytes to BGR numpy array using OpenCV."""
    nparr = np.frombuffer(file_bytes, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def render_header(title: str, subtitle: str):
    """Render standardized Telkom University brand header."""
    st.markdown(
        f"""
        <div class="telkom-header-bar"></div>
        <div class="telkom-brand-card">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <div style="color: #BA0C2F; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;">
                        Math Clinic Center &bull; Telkom University
                    </div>
                    <h2 style="margin: 2px 0 0 0; color: #0F172A; font-weight: 700; font-size: 1.4rem;">
                        {title}
                    </h2>
                    <div style="color: #64748B; font-size: 0.85rem; margin-top: 2px;">
                        {subtitle}
                    </div>
                </div>
                <div>
                    <span style="background: #FFF1F2; color: #BA0C2F; border: 1px solid #FFE4E6; padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; font-weight: 600;">
                        Tahun Akademik 2026/2027
                    </span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


# ==============================================================================
# SIDEBAR NAVIGATION
# ==============================================================================
with st.sidebar:
    st.markdown(
        """
        <div style="text-align: center; padding: 0.5rem 0 1rem 0;">
            <div style="background: #BA0C2F; color: white; width: 40px; height: 40px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; font-size: 1.2rem;">
                T
            </div>
            <h3 style="margin: 8px 0 0 0; font-size: 1.1rem; color: #0F172A;">OMR System</h3>
            <p style="font-size: 0.75rem; color: #64748B; margin: 0;">100% Deterministic OpenCV Engine</p>
        </div>
        """,
        unsafe_allow_html=True
    )
    st.divider()

    page = st.radio(
        "Navigation",
        ["Calibration", "Bulk Upload & Process", "Review", "Results"],
        index=1,
        label_visibility="collapsed"
    )

    st.divider()

    # Sidebar Quick Stats
    total_count = len(st.session_state["processed_results"])
    ok_count = sum(1 for r in st.session_state["processed_results"] if r.get("status") in ["OK", "REVIEWED"])
    rev_count = sum(1 for r in st.session_state["processed_results"] if r.get("status") in ["NEEDS_REVIEW", "AMBIGUOUS"])
    fail_count = sum(1 for r in st.session_state["processed_results"] if r.get("status") == "FAILED")

    st.markdown(f"**Current Session Stats:**")
    st.markdown(f"- Total Processed: **{total_count}**")
    st.markdown(f"- ✅ Valid (OK/Reviewed): **{ok_count}**")
    st.markdown(f"- ⚠️ Needs Review: **{rev_count}**")
    st.markdown(f"- ❌ Failed: **{fail_count}**")

    if total_count > 0:
        if st.button("🗑️ Reset Session Data", use_container_width=True):
            st.session_state["processed_results"] = []
            st.session_state["uploaded_files_cache"] = {}
            st.rerun()


# ==============================================================================
# PAGE 1: CALIBRATION
# ==============================================================================
if page == "Calibration":
    render_header("OMR Calibration & Template Designer", "Calibrate canonical canvas, fiducial markers, bubble grid regions, and answer key.")

    calib_tabs = st.tabs(["📐 Template Grid Configurator", "🔑 Answer Key Configurator (Kunci Jawaban)", "🖼️ Blank LJK Live Preview", "💾 Template JSON Management"])

    with calib_tabs[0]:
        st.subheader("Region & Grid Parameters")
        st.info("💡 Adjust start coordinates and step intervals. All coordinates are relative to the canonical canvas (1654 × 2339).")

        cfg = st.session_state["template"]
        scoring_cfg = cfg.get("scoring", {})
        id_cfg = cfg.get("identity", {})
        name_cfg = id_cfg.get("name", {})
        npm_cfg = id_cfg.get("npm", {})
        fac_cfg = id_cfg.get("faculty", {})
        surv_cfg = cfg.get("survey", {})
        math_cfg = cfg.get("mathematics", {})

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### 1. Nama Lengkap (Name)")
            n_pos = st.number_input("Name Positions (Cols)", min_value=10, max_value=30, value=int(name_cfg.get("positions", 20)), key="c_n_pos")
            n_sx = st.number_input("Name Start X", min_value=0, max_value=1654, value=int(name_cfg.get("start_x", 165)), key="c_n_sx")
            n_sy = st.number_input("Name Start Y", min_value=0, max_value=2339, value=int(name_cfg.get("start_y", 440)), key="c_n_sy")
            n_spx = st.number_input("Name Spacing X", min_value=10, max_value=60, value=int(name_cfg.get("spacing_x", 34)), key="c_n_spx")
            n_spy = st.number_input("Name Spacing Y", min_value=10, max_value=60, value=int(name_cfg.get("spacing_y", 28)), key="c_n_spy")

            st.markdown("#### 2. NPM / NIM (10 Digits)")
            m_sx = st.number_input("NPM Start X", min_value=0, max_value=1654, value=int(npm_cfg.get("start_x", 940)), key="c_m_sx")
            m_sy = st.number_input("NPM Start Y", min_value=0, max_value=2339, value=int(npm_cfg.get("start_y", 440)), key="c_m_sy")
            m_spx = st.number_input("NPM Spacing X", min_value=10, max_value=60, value=int(npm_cfg.get("spacing_x", 36)), key="c_m_spx")
            m_spy = st.number_input("NPM Spacing Y", min_value=10, max_value=60, value=int(npm_cfg.get("spacing_y", 30)), key="c_m_spy")

        with col2:
            st.markdown("#### 3. Fakultas (7 Choices)")
            f_sx = st.number_input("Faculty Start X", min_value=0, max_value=1654, value=int(fac_cfg.get("start_x", 1360)), key="c_f_sx")
            f_sy = st.number_input("Faculty Start Y", min_value=0, max_value=2339, value=int(fac_cfg.get("start_y", 440)), key="c_f_sy")
            f_spy = st.number_input("Faculty Spacing Y", min_value=10, max_value=60, value=int(fac_cfg.get("spacing_y", 36)), key="c_f_spy")

            st.markdown("#### 4. Detection & Scoring Thresholds")
            fill_th = st.slider("Fill Threshold", min_value=0.10, max_value=0.80, value=float(scoring_cfg.get("fill_threshold", 0.38)), step=0.01, key="c_fill_th")
            blank_th = st.slider("Blank Threshold", min_value=0.05, max_value=0.40, value=float(scoring_cfg.get("blank_threshold", 0.18)), step=0.01, key="c_blank_th")
            ambig_mg = st.slider("Ambiguity Margin", min_value=0.02, max_value=0.30, value=float(scoring_cfg.get("ambiguity_margin", 0.12)), step=0.01, key="c_ambig_mg")
            b_rad = st.number_input("Bubble Radius (px)", min_value=5, max_value=20, value=int(scoring_cfg.get("bubble_radius", 10)), key="c_b_rad")

        if st.button("💾 Apply & Update Active Template", type="primary", use_container_width=True):
            st.session_state["template"]["identity"]["name"].update({
                "positions": n_pos, "start_x": n_sx, "start_y": n_sy, "spacing_x": n_spx, "spacing_y": n_spy, "bubble_radius": b_rad
            })
            st.session_state["template"]["identity"]["npm"].update({
                "digits": 10, "start_x": m_sx, "start_y": m_sy, "spacing_x": m_spx, "spacing_y": m_spy, "bubble_radius": b_rad
            })
            st.session_state["template"]["identity"]["faculty"].update({
                "start_x": f_sx, "start_y": f_sy, "spacing_y": f_spy, "bubble_radius": b_rad
            })
            st.session_state["template"]["scoring"].update({
                "fill_threshold": fill_th, "blank_threshold": blank_th, "ambiguity_margin": ambig_mg, "bubble_radius": b_rad
            })
            st.success("✅ Active template configuration updated successfully!")

    with calib_tabs[1]:
        st.subheader("Pengaturan Kunci Jawaban Soal Matematika (100 Soal)")
        st.info("💡 Atur kunci jawaban resmi untuk penilaian otomatis. Skor / Nilai mahasiswa akan dihitung secara langsung berbasis kunci jawaban ini.")

        current_key = st.session_state["template"].get("mathematics", {}).get("answer_key", {})

        st.markdown("#### 1. Input Cepat (Text / CSV)")
        key_str_default = "".join([current_key.get(f"Q{i:02d}" if i < 100 else "Q100", "A") for i in range(1, 101)])
        raw_key_input = st.text_area(
            "Masukkan 100 Huruf Kunci Jawaban (Tanpa spasi / pisah koma, misal: ABCDABCD...)",
            value=key_str_default,
            height=100,
            help="Contoh: A,B,C,D... atau ABCDABCD..."
        )

        col_preset1, col_preset2, col_preset3 = st.columns(3)
        if col_preset1.button("⚡ Set Pattern A-B-C-D", use_container_width=True):
            new_k = {(f"Q{i:02d}" if i < 100 else "Q100"): ["A", "B", "C", "D"][(i - 1) % 4] for i in range(1, 101)}
            st.session_state["template"]["mathematics"]["answer_key"] = new_k
            st.success("✅ Kunci jawaban di-set ke pola A-B-C-D!")
            st.rerun()

        if col_preset2.button("⚡ Set Semua A", use_container_width=True):
            new_k = {(f"Q{i:02d}" if i < 100 else "Q100"): "A" for i in range(1, 101)}
            st.session_state["template"]["mathematics"]["answer_key"] = new_k
            st.success("✅ Kunci jawaban di-set ke semua A!")
            st.rerun()

        if col_preset3.button("⚡ Parse Text Input Di Atas", type="primary", use_container_width=True):
            cleaned = [c.upper() for c in raw_key_input.replace(",", "").replace(" ", "").strip() if c.upper() in ["A", "B", "C", "D"]]
            if len(cleaned) == 100:
                new_k = {(f"Q{i:02d}" if i < 100 else "Q100"): cleaned[i - 1] for i in range(1, 101)}
                st.session_state["template"]["mathematics"]["answer_key"] = new_k
                st.success("✅ Kunci jawaban 100 soal berhasil diperbarui dari text input!")
                st.rerun()
            else:
                st.error(f"Jumlah kunci jawaban tidak pas 100 (Terdeteksi {len(cleaned)} pilihan A-D). Mohon periksa kembali input Anda.")

        st.divider()
        st.markdown("#### 2. Grid Interaktif Kunci Jawaban (Q01 - Q100)")
        with st.expander("Tampilkan / Edit Detail Per Soal (100 Kunci Jawaban)", expanded=False):
            ak_cols = st.columns(4)
            updated_interactive_key = dict(current_key)
            for c_idx in range(4):
                start_q = c_idx * 25 + 1
                end_q = (c_idx + 1) * 25
                with ak_cols[c_idx]:
                    for q in range(start_q, end_q + 1):
                        qk = f"Q{q:02d}" if q < 100 else "Q100"
                        cur_v = current_key.get(qk, "A")
                        def_idx = ["A", "B", "C", "D"].index(cur_v) if cur_v in ["A", "B", "C", "D"] else 0
                        sel_v = st.selectbox(f"{qk}", ["A", "B", "C", "D"], index=def_idx, key=f"ak_sel_{qk}")
                        updated_interactive_key[qk] = sel_v

            if st.button("💾 Simpan Perubahan Grid", use_container_width=True):
                st.session_state["template"]["mathematics"]["answer_key"] = updated_interactive_key
                st.success("✅ Kunci jawaban grid berhasil disimpan!")
                st.rerun()

    with calib_tabs[2]:
        st.subheader("Upload Blank LJK & Test Overlay")
        uploaded_blank = st.file_uploader("Upload Blank LJK Template Image (JPG / PNG)", type=["jpg", "jpeg", "png"], key="blank_uploader")

        if uploaded_blank:
            blank_bytes = uploaded_blank.read()
            blank_bgr = load_image_from_bytes(blank_bytes)
        else:
            st.caption("ℹ️ Generating synthetic blank LJK preview from active template...")
            blank_bgr = create_blank_canonical_ljk(st.session_state["template"])

        if blank_bgr is not None:
            res_blank = process_ljk(blank_bgr, st.session_state["template"], filename="Blank_LJK.jpg")
            aligned_blank = res_blank.get("aligned_image", blank_bgr)
            overlay_img = create_omr_debug_overlay(aligned_blank, res_blank, st.session_state["template"])

            col_a, col_b = st.columns([1, 1])
            with col_a:
                st.markdown("**Original / Aligned Blank Canvas**")
                st.image(cv2.cvtColor(aligned_blank, cv2.COLOR_BGR2RGB), use_container_width=True)
            with col_b:
                st.markdown("**OMR Coordinate Grid Overlay Preview**")
                st.image(cv2.cvtColor(overlay_img, cv2.COLOR_BGR2RGB), use_container_width=True)

    with calib_tabs[3]:
        st.subheader("Template JSON Import & Export")
        template_str = json.dumps(st.session_state["template"], indent=2)

        st.download_button(
            label="📥 Download Template JSON",
            data=template_str,
            file_name="telkom_ljk_template.json",
            mime="application/json",
            use_container_width=True
        )

        st.markdown("#### Upload Custom Template JSON")
        custom_json_file = st.file_uploader("Load Template File (.json)", type=["json"], key="json_uploader")
        if custom_json_file is not None:
            try:
                loaded_cfg = json.load(custom_json_file)
                if st.button("Apply Uploaded JSON Template", type="primary"):
                    st.session_state["template"] = loaded_cfg
                    st.success("✅ Custom template loaded!")
                    st.rerun()
            except Exception as e:
                st.error(f"Invalid JSON file: {e}")

        with st.expander("View Raw JSON Content"):
            st.code(template_str, language="json")


# ==============================================================================
# PAGE 2: BULK UPLOAD & PROCESS
# ==============================================================================
elif page == "Bulk Upload & Process":
    render_header("Bulk Upload & Batch Processing", "Upload student LJK photos and process them automatically with 100% deterministic OpenCV.")

    upload_col, action_col = st.columns([2.5, 1.5])

    with upload_col:
        uploaded_files = st.file_uploader(
            "Upload LJK Photos (Single or Multiple)",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
            help="Select one or multiple LJK image files from your computer."
        )

    with action_col:
        st.markdown("#### Quick Actions")
        st.markdown(f"**Files Selected:** `{len(uploaded_files) if uploaded_files else 0}`")

        generate_sample = st.button("🧪 Generate 5 Sample LJKs (Demo)", use_container_width=True)
        if generate_sample:
            with st.spinner("Generating sample student LJK sheets..."):
                sample_students = [
                    {"name": "WAHYU SURYANINGRAT", "npm": "1301234567", "fac": 0, "skew": False},
                    {"name": "ANDI PRASETYO", "npm": "1301234568", "fac": 1, "skew": True},
                    {"name": "SITI NURHALIZA", "npm": "1301234569", "fac": 3, "skew": False},
                    {"name": "BUDI SANTOSO", "npm": "1301234570", "fac": 4, "skew": True},
                    {"name": "CITRA LESTARI", "npm": "1301234571", "fac": 2, "skew": False},
                ]
                st.session_state["uploaded_files_cache"] = {}
                for idx, s in enumerate(sample_students):
                    s_img = generate_student_ljk(
                        st.session_state["template"],
                        name=s["name"],
                        npm=s["npm"],
                        faculty_index=s["fac"],
                        warp_skew=s["skew"]
                    )
                    _, buf = cv2.imencode(".jpg", s_img)
                    st.session_state["uploaded_files_cache"][f"SAMPLE_{idx+1:03d}_{s['name'].split()[0]}.jpg"] = buf.tobytes()

            st.success(f"Generated 5 sample LJK files in session memory!")
            st.rerun()

    # Merge uploaded files into session cache
    if uploaded_files:
        for f in uploaded_files:
            st.session_state["uploaded_files_cache"][f.name] = f.read()

    cached_count = len(st.session_state["uploaded_files_cache"])

    if cached_count > 0:
        st.markdown(f"### Ready to Process `{cached_count}` LJK Submissions")

        start_btn = st.button("🚀 Start Processing Batch", type="primary", use_container_width=True)

        if start_btn:
            progress_bar = st.progress(0)
            status_text = st.empty()
            metric_cols = st.columns(4)

            m_total = metric_cols[0].empty()
            m_ok = metric_cols[1].empty()
            m_rev = metric_cols[2].empty()
            m_fail = metric_cols[3].empty()

            results = []
            c_ok = 0
            c_rev = 0
            c_fail = 0

            file_items = list(st.session_state["uploaded_files_cache"].items())
            total = len(file_items)

            for i, (fname, fbytes) in enumerate(file_items):
                status_text.text(f"Processing ({i+1}/{total}): {fname}...")
                img_bgr = load_image_from_bytes(fbytes)

                res = process_ljk(img_bgr, st.session_state["template"], filename=fname)
                results.append(res)

                if res["status"] == "OK":
                    c_ok += 1
                elif res["status"] in ["NEEDS_REVIEW", "AMBIGUOUS"]:
                    c_rev += 1
                else:
                    c_fail += 1

                # Update live metrics
                m_total.metric("Total", f"{i+1}/{total}")
                m_ok.metric("✅ OK", f"{c_ok}")
                m_rev.metric("⚠️ Needs Review", f"{c_rev}")
                m_fail.metric("❌ Failed", f"{c_fail}")

                progress_bar.progress((i + 1) / total)

            st.session_state["processed_results"] = results
            status_text.success(f"🎉 Batch processing completed! Total: {total} | OK: {c_ok} | Review: {c_rev} | Failed: {c_fail}")

    # ==========================================================================
    # MANDATORY UX REQUIREMENT: IMMEDIATELY SHOW RESULTS DATAFRAME AFTER PROCESSING
    # ==========================================================================
    if st.session_state["processed_results"]:
        st.divider()
        st.markdown("### 📊 Processed Results Table")

        df = prepare_dataframe(st.session_state["processed_results"])

        # Create clean display view
        display_df = pd.DataFrame({
            "File": df["Filename"],
            "Nama": df["Name"],
            "NPM": df["NPM"],
            "Fakultas": df["Faculty"],
            "Survey": df["Survey_Summary"],
            "Math": df["Math_Summary"],
            "Status": df["Status"].map(lambda s: "✅ OK" if s in ["OK", "REVIEWED"] else ("⚠️ Review" if s in ["NEEDS_REVIEW", "AMBIGUOUS"] else "❌ Failed")),
            "Confidence": df["Confidence"].map(lambda c: f"{c:.1f}%")
        })

        # Filter options
        filter_col, search_col = st.columns([1, 2])
        with filter_col:
            status_filter = st.selectbox("Filter Status", ["All Submissions", "Only OK", "Only Needs Review", "Only Failed"])
        with search_col:
            search_query = st.text_input("Search (Name / NPM / File)", placeholder="e.g. WAHYU or 130123...")

        filtered_df = display_df.copy()
        if status_filter == "Only OK":
            filtered_df = filtered_df[filtered_df["Status"].str.contains("OK")]
        elif status_filter == "Only Needs Review":
            filtered_df = filtered_df[filtered_df["Status"].str.contains("Review")]
        elif status_filter == "Only Failed":
            filtered_df = filtered_df[filtered_df["Status"].str.contains("Failed")]

        if search_query:
            q = search_query.lower()
            filtered_df = filtered_df[
                filtered_df["Nama"].str.lower().str.contains(q) |
                filtered_df["NPM"].str.lower().str.contains(q) |
                filtered_df["File"].str.lower().str.contains(q)
            ]

        st.dataframe(filtered_df, use_container_width=True, height=360)


# ==============================================================================
# PAGE 3: REVIEW
# ==============================================================================
elif page == "Review":
    render_header("Human-in-the-Loop Review & Correction", "Inspect and correct ambiguous or flagged LJK entries.")

    results = st.session_state["processed_results"]
    review_queue = [r for r in results if r.get("status") in ["NEEDS_REVIEW", "AMBIGUOUS"]]

    if not review_queue:
        st.markdown(
            """
            <div style="background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 12px; padding: 2rem; text-align: center; margin-top: 1rem;">
                <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">🎉</div>
                <h3 style="color: #166534; margin: 0;">No Submissions Require Review!</h3>
                <p style="color: #15803D; font-size: 0.9rem; margin-top: 0.25rem;">
                    All uploaded LJK sheets have been read deterministically with high confidence.
                </p>
            </div>
            """,
            unsafe_allow_html=True
        )
    else:
        st.markdown(f"**Items in Review Queue:** `{len(review_queue)}`")

        submission_options = [f"{r.get('filename')} | {r.get('name', 'UNKNOWN')} ({r.get('npm', 'NO_NPM')})" for r in review_queue]
        chosen_idx = st.selectbox("Select Submission to Review", range(len(submission_options)), format_func=lambda i: submission_options[i])

        target_sub = review_queue[chosen_idx]
        target_fname = target_sub.get("filename", "")

        st.markdown(f"#### Reviewing: `{target_fname}`")
        if target_sub.get("reason"):
            st.warning(f"⚠️ Flagged Reason: **{target_sub.get('reason')}**")

        rev_col1, rev_col2 = st.columns([1.2, 1.8])

        with rev_col1:
            st.markdown("**OMR Aligned Visual Crop**")
            aligned_img = target_sub.get("aligned_image")
            if aligned_img is not None:
                overlay_debug = create_omr_debug_overlay(aligned_img, target_sub, st.session_state["template"])
                st.image(cv2.cvtColor(overlay_debug, cv2.COLOR_BGR2RGB), use_container_width=True)
            else:
                st.error("No aligned image available (Alignment Failed).")

        with rev_col2:
            st.markdown("**Correction Form**")
            with st.form(key=f"form_review_{target_fname}"):
                c_name = st.text_input("Nama Lengkap", value=target_sub.get("name", ""))
                c_npm = st.text_input("NPM / NIM (10 Digits)", value=target_sub.get("npm", ""))

                fac_options = [
                    "FIF (Fakultas Informatika)",
                    "FTE (Fakultas Teknik Elektro)",
                    "FRI (Fakultas Rekayasa Industri)",
                    "FEB (Fakultas Ekonomi dan Bisnis)",
                    "FIK (Fakultas Industri Kreatif)",
                    "FKS (Fakultas Komunikasi & Ilmu Sosial)",
                    "FIT (Fakultas Ilmu Terapan)"
                ]
                current_fac = target_sub.get("faculty_full", "")
                fac_idx = 0
                for i, opt in enumerate(fac_options):
                    if opt.startswith(target_sub.get("faculty", "FIF")):
                        fac_idx = i
                        break
                c_fac = st.selectbox("Fakultas", fac_options, index=fac_idx)

                st.markdown("**Review Flagged Questions (Survey & Math)**")
                # Show flagged items specifically
                flagged_math = target_sub.get("debug", {}).get("math_details", {}).get("details", {})
                flagged_keys = [k for k, v in flagged_math.items() if v.get("status") in ["AMBIGUOUS", "MULTIPLE"]]

                math_answers_override = dict(target_sub.get("math", {}).get("answers", {}))
                for fk in flagged_keys[:5]:
                    f_detail = flagged_math[fk]
                    top_choices = [item["choice"] for item in f_detail.get("top_two", []) if item["choice"]]
                    top_choices.append("BLANK")
                    cur_ans = f_detail.get("value", "")
                    def_idx = 0
                    if cur_ans in top_choices:
                        def_idx = top_choices.index(cur_ans)
                    sel_ans = st.radio(f"{fk} Ambiguous Mark:", top_choices, index=def_idx, horizontal=True)
                    math_answers_override[fk] = "" if sel_ans == "BLANK" else sel_ans

                save_correction = st.form_submit_button("✅ Save Correction", type="primary", use_container_width=True)

                if save_correction:
                    # Update target submission in session state
                    for r in st.session_state["processed_results"]:
                        if r.get("filename") == target_fname:
                            r["name"] = c_name.strip().upper()
                            r["npm"] = c_npm.strip()
                            r["faculty"] = c_fac.split()[0]
                            r["faculty_full"] = c_fac
                            r["status"] = "REVIEWED"
                            r["confidence"] = 0.99
                            r["reason"] = "Corrected and confirmed by human operator"
                            r["math"]["answers"] = math_answers_override
                            r["math"]["valid_count"] = sum(1 for a in math_answers_override.values() if a in ["A", "B", "C", "D"])
                            r["math"]["summary"] = f"{r['math']['valid_count']}/100"
                            break

                    st.success(f"✅ Submission '{target_fname}' saved as REVIEWED!")
                    st.rerun()


# ==============================================================================
# PAGE 4: RESULTS & DETAIL VIEW
# ==============================================================================
elif page == "Results":
    render_header("Results & Analytical Export", "Comprehensive view of all read submissions, visual debug overlays, and CSV/Excel download.")

    results = st.session_state["processed_results"]

    if not results:
        st.info("ℹ️ No processed submissions yet. Please upload files in **Bulk Upload & Process** first.")
    else:
        # Summary Metrics Cards
        total_n = len(results)
        ok_n = sum(1 for r in results if r.get("status") in ["OK", "REVIEWED"])
        rev_n = sum(1 for r in results if r.get("status") in ["NEEDS_REVIEW", "AMBIGUOUS"])
        fail_n = sum(1 for r in results if r.get("status") == "FAILED")
        avg_score = float(np.mean([r.get("math", {}).get("valid_count", 0) for r in results])) if results else 0.0

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.markdown(f'<div class="stat-card"><div class="stat-val">{total_n}</div><div class="stat-lbl">Total LJK</div></div>', unsafe_allow_html=True)
        m2.markdown(f'<div class="stat-card"><div class="stat-val" style="color: #166534;">{ok_n}</div><div class="stat-lbl">Success (OK)</div></div>', unsafe_allow_html=True)
        m3.markdown(f'<div class="stat-card"><div class="stat-val" style="color: #92400E;">{rev_n}</div><div class="stat-lbl">Needs Review</div></div>', unsafe_allow_html=True)
        m4.markdown(f'<div class="stat-card"><div class="stat-val" style="color: #991B1B;">{fail_n}</div><div class="stat-lbl">Failed</div></div>', unsafe_allow_html=True)
        m5.markdown(f'<div class="stat-card"><div class="stat-val" style="color: #BA0C2F;">{avg_score:.1f}</div><div class="stat-lbl">Avg Math Score</div></div>', unsafe_allow_html=True)

        st.markdown("")

        # Export Buttons
        exp_col1, exp_col2, _ = st.columns([1, 1, 2])
        with exp_col1:
            csv_data = export_to_csv(results)
            st.download_button(
                label="📥 Download CSV Report",
                data=csv_data,
                file_name="telkom_ljk_results.csv",
                mime="text/csv",
                use_container_width=True
            )
        with exp_col2:
            excel_bytes = export_to_excel(results)
            st.download_button(
                label="📊 Download Excel Report (.xlsx)",
                data=excel_bytes,
                file_name="telkom_ljk_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

        st.divider()

        # Drill Down / Submission Detail Viewer
        st.markdown("### 🔍 Submission Detail & Visual Debugger")
        sub_list = [f"{r.get('filename')} | {r.get('name', 'N/A')} ({r.get('npm', 'N/A')})" for r in results]
        selected_sub_idx = st.selectbox("Select Submission to Inspect", range(len(sub_list)), format_func=lambda i: sub_list[i])

        sel_res = results[selected_sub_idx]

        d_col1, d_col2 = st.columns([1, 1.8])

        with d_col1:
            # Participant Card
            st.markdown(
                f"""
                <div style="background: white; border: 1px solid #E2E8F0; border-radius: 10px; padding: 1.25rem; box-shadow: 0 1px 2px rgba(0,0,0,0.03);">
                    <div style="font-weight: 700; font-size: 1.1rem; color: #0F172A; margin-bottom: 0.75rem;">
                        📄 {sel_res.get('filename')}
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #F1F5F9; font-size: 0.85rem;">
                        <span style="color: #64748B;">Nama:</span>
                        <span style="font-weight: 600; color: #0F172A;">{sel_res.get('name', 'N/A')}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #F1F5F9; font-size: 0.85rem;">
                        <span style="color: #64748B;">NPM / NIM:</span>
                        <span style="font-weight: 600; font-family: monospace; color: #0F172A;">{sel_res.get('npm', 'N/A')}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #F1F5F9; font-size: 0.85rem;">
                        <span style="color: #64748B;">Fakultas:</span>
                        <span style="font-weight: 600; color: #0F172A;">{sel_res.get('faculty_full', sel_res.get('faculty', 'N/A'))}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #F1F5F9; font-size: 0.85rem;">
                        <span style="color: #64748B;">Survey:</span>
                        <span style="font-weight: 600; color: #0F172A;">{sel_res.get('survey', {}).get('summary', '0/10')} Valid</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #F1F5F9; font-size: 0.85rem;">
                        <span style="color: #64748B;">Math Score:</span>
                        <span style="font-weight: 700; color: #BA0C2F; font-size: 1.1rem;">{sel_res.get('math', {}).get('score', 0.0)} / 100</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #F1F5F9; font-size: 0.85rem;">
                        <span style="color: #64748B;">Detail Benar / Salah:</span>
                        <span style="font-weight: 600; color: #0F172A;">
                            <span style="color: #166534;">{sel_res.get('math', {}).get('correct_count', 0)} Benar</span> | 
                            <span style="color: #991B1B;">{sel_res.get('math', {}).get('wrong_count', 0)} Salah</span>
                        </span>
                    </div>
                    <div style="display: flex; justify-content: space-between; padding: 4px 0; font-size: 0.85rem; align-items: center; margin-top: 6px;">
                        <span style="color: #64748B;">Status:</span>
                        <span class="{'badge-ok' if sel_res.get('status') in ['OK', 'REVIEWED'] else ('badge-review' if sel_res.get('status') in ['NEEDS_REVIEW', 'AMBIGUOUS'] else 'badge-failed')}">
                            {sel_res.get('status')} ({sel_res.get('confidence', 0.0)*100:.1f}%)
                        </span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

            st.markdown("#### Kuisioner (Self-Assessment)")
            s_answers = sel_res.get("survey", {}).get("answers", {})
            s_cols = st.columns(2)
            for i in range(1, 11):
                sk = f"S{i:02d}"
                s_ans = s_answers.get(sk, "-")
                c = s_cols[0] if i <= 5 else s_cols[1]
                c.markdown(f"`{sk}`: **{s_ans if s_ans else 'BLANK'}** {'✓' if s_ans in ['A','B','C','D'] else '⚠️'}")

        with d_col2:
            st.markdown("#### Mathematics Pretest Answers (100 Questions)")
            m_answers = sel_res.get("math", {}).get("answers", {})
            m_details = sel_res.get("debug", {}).get("math_details", {}).get("details", {})

            # 4-column compact grid (Q1-25, Q26-50, Q51-75, Q76-100)
            q_cols = st.columns(4)
            for col_idx in range(4):
                start_q = col_idx * 25 + 1
                end_q = (col_idx + 1) * 25
                with q_cols[col_idx]:
                    for q in range(start_q, end_q + 1):
                        qk = f"Q{q:02d}" if q < 100 else "Q100"
                        ans = m_answers.get(qk, "")
                        q_info = m_details.get(qk, {})
                        expected = q_info.get("expected", "")
                        is_corr = q_info.get("is_correct", False)

                        if not ans:
                            css_cls = "answer-cell"
                            disp = f"<b>{qk}</b>: - ({expected})"
                        elif is_corr:
                            css_cls = "answer-cell-correct"
                            disp = f"<b>{qk}</b>: {ans} ✓"
                        else:
                            css_cls = "answer-cell-wrong"
                            disp = f"<b>{qk}</b>: {ans} ✗ ({expected})"
                        st.markdown(f'<div class="{css_cls}">{disp}</div>', unsafe_allow_html=True)

        # Visual Debugger Tabs
        st.divider()
        st.markdown("#### 🛠️ Visual Debugger (Alignment & Bubble Detection)")
        debug_tabs = st.tabs(["Show OMR Overlay", "Show Aligned Image", "Show Original Image"])

        aligned_img = sel_res.get("aligned_image")
        raw_bytes = st.session_state["uploaded_files_cache"].get(sel_res.get("filename"))
        raw_bgr = load_image_from_bytes(raw_bytes) if raw_bytes else None

        with debug_tabs[0]:
            if aligned_img is not None:
                overlay_debug = create_omr_debug_overlay(aligned_img, sel_res, st.session_state["template"])
                st.image(cv2.cvtColor(overlay_debug, cv2.COLOR_BGR2RGB), use_container_width=True)
            else:
                st.warning("Alignment failed - no aligned image available.")

        with debug_tabs[1]:
            if aligned_img is not None:
                st.image(cv2.cvtColor(aligned_img, cv2.COLOR_BGR2RGB), use_container_width=True)
            else:
                st.warning("Alignment failed.")

        with debug_tabs[2]:
            if raw_bgr is not None:
                st.image(cv2.cvtColor(raw_bgr, cv2.COLOR_BGR2RGB), use_container_width=True)
            else:
                st.info("Raw image not cached.")
