# OMR LJK Reader V2

Aplikasi Optical Mark Recognition (OMR) untuk Lembar Jawaban Komputer (LJK) berbasis Python, Streamlit, dan OpenCV. 
Aplikasi ini secara khusus dirancang menggunakan **Automatic Registration Mark (RegMark) Detection** sebagai anchor utama untuk proses alignment/perspective warp. Hal ini membuatnya robust terhadap rotasi, kemiringan perspektif, dan noise kamera tanpa mengandalkan deteksi tepi kertas.

## Fitur Utama

- **Mode 1: Calibration**: 
  Deteksi otomatis 4 anchor/RegMark pada template, tentukan sistem koordinat geometris kanvas, dan setel posisi ROI bubble untuk Nama (20x26), NPM (10x10), Fakultas (7), Kuesioner (15x4), dan Soal (75x4). Ekspor konfigurasi ke `template.json`.
  
- **Mode 2: OMR Reader**:
  Memproses satu atau banyak foto (batch), meluruskan gambar berdasarkan RegMark yang dideteksi secara otomatis, menganalisis status tiap bubble menggunakan *local intensity ratio*, dan mendekode data. Mengekspor hasil menjadi CSV atau JSON.
  
- **Robust Alignment**:
  Pendeteksian RegMark di 4 sudut halaman (bukan mendeteksi garis batas kertas).

## Cara Menjalankan

1. Instal dependensi:
   ```bash
   pip install -r requirements.txt
   ```
2. Jalankan aplikasi Streamlit:
   ```bash
   streamlit run app.py
   ```

## Struktur File
- `app.py`: Aplikasi Streamlit utama.
- `core/alignment.py`: Deteksi RegMark dan perspective transform.
- `core/detector.py`: Logika lokalisasi dan ekstraksi state bubble.
- `core/decoder.py`: Logika konversi status bubble (MARKED, MULTIPLE, BLANK) ke data teks.
- `core/utils.py`: Pembuatan overlay gambar.
