# LJK Scanner v2: Math Center CSWDA

Web app (Next.js + Supabase, deploy di Vercel) untuk memindai LJK Profiling Literasi Numerasi.
Sasaran pertama **mahasiswa MK Pendidikan Karakter**, tapi LJK & aplikasinya umum (bisa dipakai ujian lain). Semua OMR berjalan **di browser HP
pengawas** (OpenCV-style deterministik, tanpa AI/OCR), dan server hanya menyimpan hasil + foto.

## Yang berubah dari v1

| v1 (Streamlit) | v2 |
|---|---|
| Siapa pun bisa upload | **Login Google**, hanya Gmail di whitelist. Setiap upload tercatat atas nama akun |
| Isi nama/ruangan manual | Pengawas **memilih kelas** dari database (32 kelas Pendikar, 1.290 peserta) |
| Bulk upload foto dari galeri | **Dihapus.** Hanya kamera live di browser, **satu foto = satu mahasiswa** |
| ArUco kecil di pojok, sering gagal (tertimpa/tipis/jauh) | **4 kotak hitam pejal 10 mm** + bar orientasi (gaya Evalbee) |
| Kamera bebas, foto apa saja diterima | Kamera **mendeteksi 4 kotak hitam secara live**, cek jarak/kemiringan/stabil, lalu **auto-jepret** |
| Sistem **silang** (X = benar) | Sistem **arsir penuh** (lihat alasan di bawah) |
| Grid Nama 20×26 + NPM 10 digit (60% halaman) | Nama ditulis tangan saja. **Bubble NIM 12 digit** dicocokkan ke daftar kelas, kalau tidak cocok pengawas pilih dari dropdown |
| Kode soal pilihan tetap | **Kode soal 3 digit diarsir seperti NIM.** Kode yang berlaku diatur per ujian di halaman Admin |
| Pernyataan kejujuran terpisah dari TTD | Pernyataan "mengerjakan secara jujur, tanpa bantuan AI maupun orang lain" tercetak tepat di atas kotak tanda tangan |
| Validasi di satu sesi Streamlit | **Validasi lintas kelas**, oleh pengawas mana pun, dengan audit log setiap perubahan |
| Nilai terlihat semua orang | **Kunci & nilai hanya admin** (dihitung di database, tidak pernah dikirim ke HP pengawas) |

### Kenapa arsir, bukan silang?
Coretan X hanya menutup ~20–35% kotak, dan di v1 huruf A/B/C/D dicetak hitam di dalam kotak.
Akibatnya kotak *kosong* pun sudah bertinta, sehingga selisih "terisi vs kosong" tipis, apalagi di
fotokopian. Bulatan yang diarsir penuh menutup 70–100% area, sementara huruf di LJK v2 dicetak
abu-abu muda (kosong ≈ 5–10%). Decoder v2 juga **relatif per nomor**: opsi tergelap dipilih kalau
cukup jauh dari opsi kedua. Kalau tidak, nomor itu ditandai **ragu/ganda** untuk validator dan tidak
ditebak diam-diam.

## Struktur

```
v2/
├── ljk/generate_ljk.py      # LJK v2 (PDF) + template.json dari SATU sumber geometri
├── ljk/build_docx.js        # LJK v2 versi Word (latar OMR terkunci + teks editable)
├── ljk/synth.py             # generator foto sintetis untuk tes
├── public/ljk/LJK_v2.pdf    # template cetak (A4)
├── public/ljk/LJK_v2.docx   # template versi Word
├── lib/omr/                 # engine OMR TypeScript (browser & Node)
│   ├── markers.ts           #   deteksi 4 kotak hitam + orientasi + verifikasi template
│   ├── read.ts / decode.ts  #   sampling bubble + keputusan relatif per nomor
│   ├── quality.ts           #   px/mm, sudut, ketajaman
│   ├── grade.ts / nim.ts    #   penilaian & pencocokan NIM ke roster
│   └── omr.test.ts          #   20 foto sintetis (miring, blur, WA, fotokopi, terbalik, cetak dari Word, dll.)
├── components/scan/         # CameraScanner (live + auto-capture), ScanFlow (review & simpan)
├── components/validasi/     # ValidationEditor
├── app/                     # halaman: login, beranda, scan, validasi, rekap, admin, demo
├── supabase/migrations/     # skema + RLS + storage + RPC
└── scripts/seed_from_excel.py  # seed kelas/peserta/kunci/whitelist dari Excel Math Center
```

## Setup (sekali saja)

### 1. Supabase
1. Buat project baru di supabase.com (region Singapore).
2. **SQL Editor →** jalankan `supabase/migrations/0001_init.sql`, lalu `0002_kode_soal_digit.sql`.
3. Buat seed dari Excel (file hasilnya berisi data pribadi, **jangan di-commit**):
   ```bash
   python v2/scripts/seed_from_excel.py \
     --kelas "data/Kelas_Pendidikan_Karakter.xlsx" \
     --kunci "Komposisi soal dan kunci/kj192-273-365-487 REVISI (untuk unggah ke aplikasi).xlsx" \
     --pengawas "output/04_Penjadwalan_Pengawas_FIF/jadwal_pengawas_pendidikan_karakter.xlsx" \
     --admin email-admin@gmail.com \
     --out "output/05_LJK_v2/seed_pendikar_2026.sql"
   ```
   Lalu jalankan file SQL itu di SQL Editor. Pengawas yang belum punya Gmail di sheet bisa
   ditambahkan nanti dari halaman **Admin**.

### 2. Login Google
1. Google Cloud Console → *APIs & Services → Credentials →* **OAuth client ID** (Web application).
   - Authorized redirect URI: `https://<project-ref>.supabase.co/auth/v1/callback`
2. Supabase → *Authentication → Providers → Google*: aktifkan, isi Client ID & Secret.
3. Supabase → *Authentication → URL Configuration*:
   - Site URL: `https://<app>.vercel.app`
   - Redirect URLs: `https://<app>.vercel.app/auth/callback`, `https://*-<team>.vercel.app/auth/callback` (preview), `http://localhost:3000/auth/callback`

### 3. Vercel
1. Import repo ini → **Root Directory: `v2`** (framework Next.js terdeteksi otomatis).
2. Environment variables:
   - `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (Supabase → Settings → API)
   - `NEXT_PUBLIC_DEMO_MODE=1` *(opsional, hanya untuk Preview)*: membuka `/demo` & `/demo/validasi` tanpa login
3. Deploy. Kamera butuh HTTPS, dan domain Vercel sudah HTTPS.

## Pengembangan lokal

```bash
cd v2
cp .env.example .env.local   # isi URL & anon key
npm install
npm run dev                  # http://localhost:3000  (kamera jalan di localhost)
npm test                     # tes engine OMR (20 foto sintetis)
npm run typecheck
python ljk/generate_ljk.py   # kalau mengubah desain LJK: PDF + template.json ikut diperbarui
node ljk/build_docx.js       # lalu bangun ulang versi Word
python ljk/synth.py          # bikin ulang foto uji
```

## Alur kerja

**Pengawas (HP):** Login Google → *Scan LJK* → pilih kelas → *Buka kamera* → arahkan ke LJK
sampai bingkai hijau. Foto diambil otomatis, lalu cek mahasiswa (NIM otomatis dicocokkan ke
daftar kelas; kalau tidak cocok, pilih dari dropdown) dan kode soal → *Simpan & foto berikutnya*.

**Validator (siapa pun yang terdaftar):** *Validasi* → pilih kelas mana pun → foto + overlay di
kiri, jawaban di kanan (yang ragu/ganda disorot) → koreksi bila perlu → **Valid** / **Tolak**
(dengan alasan). Setiap perubahan tercatat di riwayat.

**Admin:** *Rekap* (nilai, unduh Excel/CSV: sheet Rekap Nilai, Belum Upload, Ringkasan Kelas),
*Admin* (whitelist pengawas, daftar kode soal + kunci jawaban per kode).

## LJK versi Word (`LJK_v2.docx`)
Semua yang dibaca mesin (4 kotak hitam, bar orientasi, bulatan, bingkai) adalah **satu gambar latar
600 dpi yang terkunci** di belakang teks. Teks judul, petunjuk, pernyataan & footer berupa kotak teks
yang **boleh diedit**. Aturannya:
- Jangan memindah, memotong, atau mengubah ukuran gambar latar.
- Edit teks seperlunya. Kalau terlalu panjang, perkecil font agar tetap di dalam kotaknya.
- Cetak dengan skala **100% / Actual size** (bukan *Fit to page*) di kertas A4.
- Perlu ubah posisi bulatan / jumlah soal? Ubah `ljk/generate_ljk.py`, jangan diubah di Word,
  karena `template.json` aplikasi harus ikut berubah.

## Keamanan
- RLS di semua tabel: akun di luar whitelist tidak bisa membaca/menulis apa pun.
- Pengawas tidak bisa membaca tabel `kunci`, dan nilai hanya lewat RPC `rekap_nilai` (cek admin).
- `uploaded_by`, `validated_by`, foto, dan hasil OMR mentah dikunci trigger, jadi tidak bisa dipalsukan.
- Satu LJK aktif per NIM (unique index). Upload ulang harus memilih "ganti" secara eksplisit.
- Foto di bucket privat `ljk`, ditampilkan lewat signed URL 1 jam.

## Batasan & langkah berikutnya
- Engine baru diuji pada **foto sintetis** (termasuk kasus sulit). Sebelum dipakai massal, **cetak
  LJK v2 dan uji 20–30 lembar asli** (pensil 2B, HP pengawas yang beragam, fotokopian), lalu
  sesuaikan `QUALITY_LIMITS` / ambang di `decode.ts` bila perlu.
- Kunci jawaban saat ini 50 soal per kode. LJK menyediakan 75 slot, dan hanya nomor di kunci yang dinilai.
- Belum ada mode offline: upload butuh koneksi saat menyimpan.
