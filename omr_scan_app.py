"""OMR LJK — Scan Cepat (minimal).

Alat Streamlit ringkas untuk memindai banyak foto LJK sekaligus dan memeriksa hasil
baca terhadap template kalibrasi yang sedang dipakai — dipakai untuk menguji akurasi
kalibrasi sebelum model ini diadopsi ke server VPS Math Center.

Memakai scanner/service.py (pipeline yang sama persis dengan app.py produksi, dijaga
oleh tests/regression) sehingga hasilnya identik dan siap dipindahkan ke FastAPI
(server/) tanpa perubahan logika.
"""
import os
from datetime import datetime

import pandas as pd
import streamlit as st

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

from core.pdf_utils import iter_images_from_file
from scanner.service import (
    SCAN_MAX_SIDE,
    get_default_template_path,
    load_default_template,
    process_single_page,
)

DEFAULT_LOCAL_FOLDER = (
    "/Users/wahyu/Library/CloudStorage/OneDrive-TelkomUniversity/wahyu-data/"
    "math-center-data/sample foto/50 foto - Relawan MC Feri Rahmansyah"
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")

st.set_page_config(page_title="OMR LJK — Scan Cepat", page_icon="🔍", layout="wide")
st.title("🔍 OMR LJK — Scan Cepat")

template = load_default_template()
tpl_path = get_default_template_path()
if not template:
    st.error(f"Template kalibrasi tidak ditemukan: `{tpl_path}`")
    st.stop()
st.caption(
    f"Template kalibrasi aktif: `{os.path.relpath(tpl_path)}` · "
    f"canvas {template.get('canvas', {}).get('width')}×{template.get('canvas', {}).get('height')} · "
    f"{len(template.get('fields', {}))} blok field"
)


class _LocalFile:
    """Bungkus path lokal agar punya `.name`/`.size`/`.getvalue()` seperti UploadedFile,
    supaya iter_images_from_file() bisa memproses foto dari folder maupun dari uploader."""

    def __init__(self, path):
        self.name = os.path.basename(path)
        self.size = os.path.getsize(path)
        self._path = path

    def getvalue(self):
        with open(self._path, "rb") as f:
            return f.read()


uploaded_files = st.file_uploader(
    "Unggah foto LJK (bisa pilih banyak sekaligus)",
    type=["pdf", "jpg", "jpeg", "png", "heic", "heif", "webp"],
    accept_multiple_files=True,
    key="omr_uploader",
)

with st.expander("Muat dari folder lokal (mode developer)"):
    folder = st.text_input("Path folder foto", value=DEFAULT_LOCAL_FOLDER)
    if st.button("Muat semua foto dari folder ini"):
        if os.path.isdir(folder):
            paths = sorted(
                os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(IMAGE_EXTS)
            )
            st.session_state["local_files"] = paths
            st.toast(f"{len(paths)} foto ditemukan di folder.")
        else:
            st.error("Folder tidak ditemukan.")
            st.session_state["local_files"] = []

sources = list(uploaded_files or []) + [_LocalFile(p) for p in st.session_state.get("local_files", [])]
sig = tuple((s.name, s.size) for s in sources)

if sources and st.session_state.get("_scanned_sig") != sig:
    results, statuses, page_refs = [], [], []
    n = len(sources)
    prog = st.progress(0.0, text=f"Memindai 0/{n}...")
    for i, src in enumerate(sources):
        try:
            for page_idx, (doc_name, img_bgr) in enumerate(iter_images_from_file(src, target_dpi=200, max_side=SCAN_MAX_SIDE)):
                rec, prev = process_single_page(img_bgr, doc_name, template, "", "-", {}, None, with_overlay=False)
                del img_bgr
                results.append(rec)
                statuses.append("Terbaca" if "DETECTED" in prev.get("status", "") else "Gagal Alignment")
                page_refs.append((i, page_idx))
        except Exception as e:
            results.append({"File": src.name})
            statuses.append(f"Error: {e}")
            page_refs.append((i, 0))
        prog.progress((i + 1) / n, text=f"Memindai {i + 1}/{n} — {src.name}")
    prog.empty()
    st.session_state["_scanned_sig"] = sig
    st.session_state["_scan_results"] = results
    st.session_state["_scan_statuses"] = statuses
    st.session_state["_scan_page_refs"] = page_refs

results = st.session_state.get("_scan_results", [])
statuses = st.session_state.get("_scan_statuses", [])
page_refs = st.session_state.get("_scan_page_refs", [])

if not sources:
    st.info("Unggah foto atau muat dari folder lokal untuk mulai memindai.")
elif results:
    n_ok = statuses.count("Terbaca")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Lembar", len(results))
    c2.metric("Berhasil Dipindai", n_ok)
    c3.metric("Gagal Alignment", len(results) - n_ok)

    df = pd.DataFrame(results)
    df.insert(0, "Status", statuses)
    priority_cols = ["Status", "File", "NPM", "Nama Mahasiswa", "Fakultas (LJK)", "Kode Soal", "Jawaban Terisi"]
    shown_cols = [c for c in priority_cols if c in df.columns]
    st.dataframe(df[shown_cols], width="stretch", hide_index=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Unduh CSV (semua kolom)",
        csv,
        file_name=f"omr_scan_{datetime.now():%Y%m%d_%H%M%S}.csv",
        mime="text/csv",
    )

    st.divider()
    st.subheader("Periksa Visual per Lembar")
    labels = [f"{r.get('File', f'#{i}')} — {statuses[i]}" for i, r in enumerate(results)]
    idx = st.selectbox("Pilih lembar", options=range(len(labels)), format_func=lambda i: labels[i])

    if st.button("Tampilkan overlay pembacaan"):
        src_i, target_page = page_refs[idx]
        src = sources[src_i]
        overlay = None
        for page_idx, (doc_name, img_bgr) in enumerate(iter_images_from_file(src, target_dpi=200, max_side=SCAN_MAX_SIDE)):
            if page_idx == target_page:
                _, prev = process_single_page(img_bgr, doc_name, template, "", "-", {}, None, with_overlay=True)
                overlay = prev.get("overlay")
                break
        if overlay is not None:
            st.image(overlay, channels="BGR", width="stretch")
        else:
            st.warning("Tidak bisa membuat overlay untuk lembar ini.")

        rec = results[idx]
        soal = sorted((k, v) for k, v in rec.items() if k.lower().startswith("soal_"))
        kuis = sorted((k, v) for k, v in rec.items() if k.lower().startswith("q") and len(k) <= 5)
        colA, colB = st.columns(2)
        if kuis:
            colA.markdown("**Kuisioner**")
            colA.dataframe(pd.DataFrame(kuis, columns=["No", "Jawaban"]), hide_index=True, width="stretch")
        if soal:
            colB.markdown("**Jawaban Soal**")
            colB.dataframe(pd.DataFrame(soal, columns=["No", "Jawaban"]), hide_index=True, width="stretch")
