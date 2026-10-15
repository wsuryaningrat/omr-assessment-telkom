"""Profil NIM per KELAS (bukan per prodi) -- utk tiap kelas, lihat kombinasi (jumlah digit, N digit awal)
yang muncul & contoh NIM-nya. Satu kelas idealnya cuma punya SATU kombinasi konsisten; kelas yang punya
lebih dari satu kombinasi berarti ada NIM yang nyasar/salah format di kelas itu -- kandidat yang perlu
dicek & dikoreksi manual pada lembar yang sudah divalidasi.

Logika inti ada di core/nim_profile.py (profile_nim_by_kelas) -- dipakai bareng gaya CLI yang sama
dengan tools/nim_profile.py (grouping per prodi). Beda skrip krn beda dimensi analisa (kelas+prefix vs
prodi+panjang digit), bukan krn harus duplikasi logikanya.

    python tools/nim_profile_by_kelas.py \
        --source "https://docs.google.com/spreadsheets/d/<ID_SUMBER>/edit?gid=<GID>" \
        --output "https://docs.google.com/spreadsheets/d/<ID_OUTPUT>/edit?gid=0"

Kredensial: --credentials <path-ke-json-atau-toml>, ATAU set env GSHEET_CREDENTIALS (format sama
dengan server/sheets.py -- isi JSON mentah, path .json, atau path .toml Streamlit). Service account
HARUS sudah di-invite (Share) ke KEDUA sheet (sumber & output) sbg Editor.

Kolom NIM & Kelas di sheet sumber dideteksi otomatis (cari kata "nim"/"npm" utk NIM, "kelas" utk kelas
-- cocok dgn header "Kelas Matakuliah" dkk). Override lewat --nim-col / --kelas-col kalau meleset.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.nim_profile import parse_sheet_url, profile_nim_by_kelas  # noqa: E402


def load_credentials_dict(spec: str) -> dict:
    spec = spec.strip()
    if spec.lstrip().startswith("{"):
        return json.loads(spec)
    if spec.lower().endswith(".toml"):
        import tomllib
        with open(spec, "rb") as f:
            cfg = tomllib.load(f)["connections"]["gsheets"]
        keys = ("type", "project_id", "private_key_id", "private_key", "client_email", "client_id",
                "auth_uri", "token_uri", "auth_provider_x509_cert_url", "client_x509_cert_url", "universe_domain")
        return {k: cfg[k] for k in keys if k in cfg}
    with open(spec, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="URL spreadsheet sumber (berisi data NIM + kelas)")
    ap.add_argument("--output", required=True, help="URL spreadsheet tujuan (hasil profil ditulis sbg tab baru)")
    ap.add_argument("--credentials", default=os.environ.get("GSHEET_CREDENTIALS", ""),
                     help="Isi JSON / path .json / path .toml kredensial service account (default: env GSHEET_CREDENTIALS)")
    ap.add_argument("--nim-col", default=None, help="Nama kolom NIM di sheet sumber (override deteksi otomatis)")
    ap.add_argument("--kelas-col", default=None, help="Nama kolom Kelas di sheet sumber (override deteksi otomatis)")
    ap.add_argument("--prefix-len", type=int, default=2, help="Berapa digit awal NIM yg dibandingkan (default: 2)")
    ap.add_argument("--output-tab", default="NIM Profile by Kelas", help="Nama tab baru di sheet output")
    args = ap.parse_args()

    if not args.credentials:
        raise SystemExit("Kredensial belum diisi -- pakai --credentials atau set env GSHEET_CREDENTIALS.")

    try:
        import gspread
    except ImportError:
        raise SystemExit("Perlu paket 'gspread' -- pasang dulu: pip install gspread") from None

    gc = gspread.service_account_from_dict(load_credentials_dict(args.credentials))

    src_id, src_gid = parse_sheet_url(args.source)
    src_sh = gc.open_by_key(src_id)
    src_ws = src_sh.get_worksheet_by_id(src_gid) if src_gid is not None else src_sh.sheet1
    print(f"Sumber: '{src_sh.title}' / tab '{src_ws.title}'", file=sys.stderr)

    rows = src_ws.get_all_records()
    try:
        result = profile_nim_by_kelas(rows, args.nim_col, args.kelas_col, args.prefix_len)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    print(f"Kolom terdeteksi -- NIM: '{result['columns']['nim']}', Kelas: '{result['columns']['kelas']}'", file=sys.stderr)
    print(f"Total baris diproses: {result['total_rows']} (dilewati krn NIM kosong: {result['skipped_blank']})", file=sys.stderr)
    print(f"Kelas dengan >1 kombinasi (jumlah digit, {args.prefix_len} digit awal): {result['flagged_kelas_count']}", file=sys.stderr)

    out_rows = [["Kelas", "Jumlah Digit", f"{args.prefix_len} Digit Awal", "Jumlah", "Contoh NIM", "Catatan"]]
    for k in result["by_kelas"]:
        for item in k["items"]:
            note = "⚠ Kelas ini punya >1 kombinasi digit/awalan -- cek manual" if item["flagged"] else ""
            out_rows.append([k["kelas"], item["length"], item["prefix"], item["count"],
                             ", ".join(item["examples"]), note])

    out_id, _ = parse_sheet_url(args.output)
    out_sh = gc.open_by_key(out_id)
    try:
        out_ws = out_sh.worksheet(args.output_tab)
        out_ws.clear()
    except gspread.WorksheetNotFound:
        out_ws = out_sh.add_worksheet(title=args.output_tab, rows=max(100, len(out_rows) + 10), cols=10)
    out_ws.update(values=out_rows, range_name="A1")
    print(f"Selesai -- ditulis ke tab '{args.output_tab}' di '{out_sh.title}' ({len(out_rows)} baris).", file=sys.stderr)


if __name__ == "__main__":
    main()
