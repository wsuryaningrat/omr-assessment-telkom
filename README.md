# Telkom University Math Center - Full OMR Calibration & Testing System

Aplikasi web **Streamlit Full OMR Calibration & Testing** untuk Lembar Jawaban Komputer (LJK) mahasiswa Telkom University menggunakan **OpenCV 100% deterministic** (zero OCR, zero LLM, zero AI Vision).

---

## Fitur Utama

1. **Calibration & Template Designer**:
   - Upload Blank LJK dan periksa alignment 4 corner markers (ArUco / fiducials).
   - Konfigurasi berbasis region & grid untuk:
     - **Nama Lengkap** (20-25 kolom, A-Z)
     - **NPM** (10 digit, 0-9)
     - **Fakultas** (7 pilihan: FIF, FTE, FRI, FEB, FIK, FKS, FIT)
     - **Kuisioner / Self-Assessment** (10 pertanyaan, pilihan A-D)
     - **Jawaban Soal Matematika** (100 pertanyaan, pilihan A-D)
   - Simpan dan ekspor template ke `template.json`.

2. **Bulk Upload & Batch Processing**:
   - Upload banyak foto LJK mahasiswa sekaligus (`st.file_uploader(accept_multiple_files=True)`).
   - Generator sampel sintetis untuk uji coba instan (5 mahasiswa).
   - Indikator progres animasi real-time (`Processed: X`, `Review: Y`, `Failed: Z`).
   - **Tampilan tabel interaktif langsung** (`st.dataframe`) begitu pemrosesan selesai tanpa perlu membuka file satu per satu.

3. **Human-in-the-Loop Review**:
   - Antarmuka khusus untuk entri berstatus `NEEDS_REVIEW` atau `AMBIGUOUS`.
   - Menampilkan visual crop / overlay OMR dan form koreksi (Nama, NPM, Fakultas, Kuisioner, Soal Matematika).
   - Satu klik tombol `Save Correction` untuk mengubah status menjadi `REVIEWED`.

4. **Results & Analytical Export**:
   - Kartu metrik ringkasan (Total LJK, Sukses OK, Needs Review, Gagal, Rata-rata Skor).
   - Detail jawaban peserta (Ringkasan Diri, 10 Kuisioner, dan 100 Jawaban Matematika dalam grid 4 kolom).
   - Visual Debugger 3 mode:
     - `Show OMR Overlay` (menandai marker, posisi bulatan, arsiran terdeteksi hijau/kuning/merah, skor, dan label).
     - `Show Aligned Image`
     - `Show Original Image`
   - Ekspor:
     - **Download CSV**: tabel flat komprehensif.
     - **Download Excel (.xlsx)**: multi-sheet (`Results`, `Review`, `Processing Log`).

---

## Struktur Folder

```text
upload-apps/
├── app.py                     # Streamlit Main UI
├── requirements.txt           # Dependencies
├── .streamlit/
│   └── config.toml            # Telkom Red UI theme
├── omr/
│   ├── __init__.py
│   ├── alignment.py           # ArUco, fiducial markers & quality gate
│   ├── preprocessing.py       # Grayscale, Otsu, adaptive thresholding
│   ├── bubbles.py             # Circular mask fill & intensity scoring
│   ├── identity.py            # Decoders for Nama, NPM, Fakultas
│   ├── survey.py              # Decoders for Kuisioner (10 Qs)
│   ├── mathematics.py         # Decoders for Math 1-100 (100 Qs)
│   ├── validation.py          # Ambiguity detection & confidence scoring
│   └── pipeline.py            # process_ljk(image, template)
├── templates/
│   └── default_template.json  # Canonical A4 template for Telkom LJK
├── utils/
│   ├── __init__.py
│   ├── export.py              # CSV & Excel multi-sheet export
│   └── visualizer.py          # High-precision debug overlay renderer
└── tests/
    ├── generate_synthetic.py  # Synthetic LJK generator
    └── test_omr_pipeline.py   # Automated unit & integration tests
```

---

## Cara Menjalankan

### 1. Jalankan Unit Tests
```bash
.venv/bin/python -m unittest tests/test_omr_pipeline.py
```

### 2. Jalankan Aplikasi Streamlit
```bash
.venv/bin/streamlit run app.py
```
Aplikasi akan terbuka di browser Anda pada alamat `http://localhost:8501`.
