"""Buat seed SQL (kelas, peserta, kunci, whitelist pengawas) dari Excel Math Center.

File SQL berisi data pribadi (nama/NIM mahasiswa, email pengawas) — JANGAN di-commit
ke repo (repo ini publik). Simpan di folder data lokal dan jalankan di Supabase SQL Editor.

Contoh:
    python v2/scripts/seed_from_excel.py \
        --kelas  "data/Kelas_Pendidikan_Karakter.xlsx" \
        --kunci  "Komposisi soal dan kunci/kj192-273-365-487 REVISI (untuk unggah ke aplikasi).xlsx" \
        --pengawas "output/04_Penjadwalan_Pengawas_FIF/jadwal_pengawas_pendidikan_karakter.xlsx" \
        --admin wahyusuryaningrat@gmail.com \
        --out "output/05_LJK_v2/seed_pendikar_2026.sql"
"""
import argparse
import json
import os
import re

import openpyxl

UJIAN_ID = "pendikar-2026"
UJIAN_NAMA = "Profiling Literasi Numerasi — MK Pendidikan Karakter 2026"
TEMPLATE_VERSION = "ljk-v2-2"


def q(v):
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return "null"
    return "'" + str(v).strip().replace("'", "''") + "'"


def read_kelas(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Jadwal Kelas"]
    kelas = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r[2]:
            continue
        kelas.append({
            "id": str(r[2]).strip(), "prodi": r[1], "kode_mk": r[3], "dosen": r[4], "ruangan": r[8],
            "hari": r[9], "waktu": r[10], "jumlah": r[11],
        })
    peserta = []
    ws = wb["Gabungan"]
    header = [str(c).strip() if c else "" for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    idx = {h: i for i, h in enumerate(header)}
    for r in ws.iter_rows(min_row=2, values_only=True):
        nim = r[idx["NIM"]]
        if not nim:
            continue
        nim = re.sub(r"\D", "", str(nim).split(".")[0])
        peserta.append({
            "kelas": str(r[idx["Kelas MK"]]).strip(), "nim": nim, "nama": str(r[idx["Nama"]]).strip(),
            "kelas_asal": r[idx.get("Kelas", -1)] if "Kelas" in idx else None,
            "status": r[idx["Status"]] if "Status" in idx else None,
        })
    return kelas, peserta


def read_kunci(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        m = re.search(r"(\d{3})", ws.title)
        if not m:
            continue
        kode = m.group(1)
        jaw = {}
        for r in ws.iter_rows(values_only=True):
            if r[0] is None or r[1] is None:
                continue
            try:
                no = int(r[0])
            except (TypeError, ValueError):
                continue
            v = str(r[1]).strip().upper()
            if v in ("*", "ALL", "SEMUA", "BONUS"):
                jaw[str(no)] = ["A", "B", "C", "D"]
            else:
                opts = sorted(set(re.findall(r"[A-D]", v)))
                if opts:
                    jaw[str(no)] = opts
        out[kode] = jaw
    return out


def read_pengawas(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Daftar Pengawas"]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        nama, hp, gmail, ket = r[1], r[3], r[4], r[5]
        if not nama or not gmail or "@" not in str(gmail):
            continue
        rows.append({"email": str(gmail).strip().lower(), "nama": str(nama).strip(), "hp": hp, "ket": ket})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kelas", required=True)
    ap.add_argument("--kunci", required=True)
    ap.add_argument("--pengawas")
    ap.add_argument("--admin", action="append", default=[])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    kelas, peserta = read_kelas(a.kelas)
    kunci = read_kunci(a.kunci)
    pengawas = read_pengawas(a.pengawas) if a.pengawas else []

    L = ["-- Seed LJK v2 (berisi data pribadi — jangan di-commit). Jalankan SETELAH 0001 & 0002.", "begin;"]
    kodes = "{" + ",".join(sorted(kunci)) + "}"
    L.append(
        f"insert into public.ujian(id, nama, template_version, kode_soal) values ({q(UJIAN_ID)}, {q(UJIAN_NAMA)}, {q(TEMPLATE_VERSION)}, {q(kodes)}::text[]) "
        "on conflict (id) do update set nama = excluded.nama, template_version = excluded.template_version, kode_soal = excluded.kode_soal;"
    )
    for k in kelas:
        L.append(
            "insert into public.kelas(id, ujian_id, prodi, kode_mk, dosen, ruangan, hari, waktu, jumlah_mahasiswa) values "
            f"({q(k['id'])}, {q(UJIAN_ID)}, {q(k['prodi'])}, {q(k['kode_mk'])}, {q(k['dosen'])}, {q(k['ruangan'])}, {q(k['hari'])}, {q(k['waktu'])}, {k['jumlah'] or 'null'}) "
            "on conflict (id) do update set prodi = excluded.prodi, kode_mk = excluded.kode_mk, dosen = excluded.dosen, ruangan = excluded.ruangan, hari = excluded.hari, waktu = excluded.waktu, jumlah_mahasiswa = excluded.jumlah_mahasiswa;"
        )
    known = {k["id"] for k in kelas}
    skipped = 0
    for p in peserta:
        if p["kelas"] not in known:
            skipped += 1
            continue
        L.append(
            f"insert into public.peserta(kelas_id, nim, nama, kelas_asal, status) values ({q(p['kelas'])}, {q(p['nim'])}, {q(p['nama'])}, {q(p['kelas_asal'])}, {q(p['status'])}) "
            "on conflict (kelas_id, nim) do update set nama = excluded.nama, kelas_asal = excluded.kelas_asal, status = excluded.status;"
        )
    for kode, jaw in kunci.items():
        L.append(
            f"insert into public.kunci(ujian_id, kode_soal, jawaban) values ({q(UJIAN_ID)}, {q(kode)}, {q(json.dumps(jaw))}::jsonb) "
            "on conflict (ujian_id, kode_soal) do update set jawaban = excluded.jawaban, updated_at = now();"
        )
    for email in a.admin:
        L.append(f"insert into public.pengawas(email, nama, role) values ({q(email.lower())}, 'Admin', 'admin') on conflict (email) do update set role = 'admin', aktif = true;")
    for p in pengawas:
        L.append(
            f"insert into public.pengawas(email, nama, no_hp, keterangan) values ({q(p['email'])}, {q(p['nama'])}, {q(p['hp'])}, {q(p['ket'])}) on conflict (email) do nothing;"
        )
    L.append("commit;")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"kelas={len(kelas)} peserta={len(peserta) - skipped} (dilewati {skipped}) kunci={ {k: len(v) for k, v in kunci.items()} } pengawas_whitelist={len(pengawas)} admin={len(a.admin)}")
    print("->", a.out)


if __name__ == "__main__":
    main()
