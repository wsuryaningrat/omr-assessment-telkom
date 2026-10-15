"""Profil panjang digit NIM per program studi, dari sheet referensi -> ditulis ke sheet output.

Dipakai utk menentukan panjang NIM yang "normal" per prodi (mis. 10 digit vs 12 digit), sebelum
mengoreksi NIM pada lembar yang sudah divalidasi di aplikasi OMR. Logika inti (deteksi kolom &
pengelompokan) ada di core/nim_profile.py, dipakai bareng dgn GET /api/admin/nim-profile (tampilan live
di UI super admin, tab Summary -- lihat server/admin.py) -- skrip CLI ini bedanya nulis hasilnya ke
TAB BARU di sheet lain (pakai service account langsung, tanpa lewat server berjalan).

    python tools/nim_profile.py \
        --source "https://docs.google.com/spreadsheets/d/<ID_SUMBER>/edit?gid=<GID>" \
        --output "https://docs.google.com/spreadsheets/d/<ID_OUTPUT>/edit?gid=0"

Kredensial: --credentials <path-ke-json-atau-toml>, ATAU set env GSHEET_CREDENTIALS (format sama
dengan yang dipakai server/sheets.py -- isi JSON mentah, path ke .json, atau path ke secrets.toml gaya
Streamlit). Service account di kredensial itu HARUS sudah di-invite (Share) ke KEDUA sheet (sumber &
output) sbg Editor -- kalau cuma invite ke salah satu, baca/tulis yang lain akan gagal 403.

Kolom NIM & Program Studi di sheet sumber dideteksi otomatis dari nama header (cari kata "nim"/"npm"
utk kolom NIM, "prodi"/"program studi" utk kolom prodi). Kalau deteksi salah/meleset, override lewat
--nim-col "Nama Kolom Persis" / --prodi-col "Nama Kolom Persis".
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.nim_profile import parse_sheet_url, profile_nim_lengths  # noqa: E402


def load_credentials_dict(spec: str) -> dict:
    """Spt server/sheets.py GSheetsClient._client -- terima isi JSON mentah, path .json, atau path .toml
    (format secrets.toml Streamlit, [connections.gsheets])."""
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
    ap.add_argument("--source", required=True, help="URL spreadsheet sumber (berisi data NIM + prodi)")
    ap.add_argument("--output", required=True, help="URL spreadsheet tujuan (hasil profil ditulis sbg tab baru)")
    ap.add_argument("--credentials", default=os.environ.get("GSHEET_CREDENTIALS", ""),
                     help="Isi JSON / path .json / path .toml kredensial service account (default: env GSHEET_CREDENTIALS)")
    ap.add_argument("--nim-col", default=None, help="Nama kolom NIM di sheet sumber (override deteksi otomatis)")
    ap.add_argument("--prodi-col", default=None, help="Nama kolom Program Studi di sheet sumber (override deteksi otomatis)")
    ap.add_argument("--output-tab", default="NIM Digit Profile", help="Nama tab baru di sheet output (default: 'NIM Digit Profile')")
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
        result = profile_nim_lengths(rows, args.nim_col, args.prodi_col)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    print(f"Kolom terdeteksi -- NIM: '{result['columns']['nim']}', Prodi: '{result['columns']['prodi']}'", file=sys.stderr)
    print(f"Total baris diproses: {result['total_rows']} (dilewati krn NIM kosong: {result['skipped_blank']})", file=sys.stderr)
    print("Ringkasan keseluruhan panjang digit:", {o['length']: o['count'] for o in result['overall']}, file=sys.stderr)

    out_rows = [["Program Studi", "Panjang Digit NIM", "Jumlah", "Contoh NIM", "Catatan"]]
    for p in result["by_prodi"]:
        for item in p["lengths"]:
            note = "⚠ Prodi ini punya >1 panjang digit NIM -- cek manual" if item["flagged"] else ""
            out_rows.append([p["prodi"], item["length"], item["count"], ", ".join(item["examples"]), note])

    out_rows.append([])
    out_rows.append(["== Ringkasan keseluruhan =="])
    out_rows.append(["Panjang Digit NIM", "Total"])
    for o in result["overall"]:
        out_rows.append([o["length"], o["count"]])

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
