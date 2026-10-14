"""Profil panjang digit NIM per program studi, dari sheet referensi -> ditulis ke sheet output.

Dipakai utk menentukan panjang NIM yang "normal" per prodi (mis. 10 digit vs 12 digit), sebelum
mengoreksi NIM pada lembar yang sudah divalidasi di aplikasi OMR (lihat server/admin.py bulk-edit /
GET /api/admin/npm-duplicates utk sisi koreksi datanya).

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
import re
import sys
from collections import defaultdict
from urllib.parse import urlparse, parse_qs


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


def sheet_id_and_gid(url: str):
    parts = urlparse(url)
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", parts.path)
    if not m:
        raise SystemExit(f"URL spreadsheet tak dikenali: {url}")
    sheet_id = m.group(1)
    gid = None
    qs = parse_qs(parts.query)
    if "gid" in qs:
        gid = int(qs["gid"][0])
    elif parts.fragment:
        fm = re.search(r"gid=(\d+)", parts.fragment)
        if fm:
            gid = int(fm.group(1))
    return sheet_id, gid


def pick_column(headers: list, keywords: list, override: str | None) -> str:
    if override:
        if override not in headers:
            raise SystemExit(f"Kolom '{override}' tak ditemukan. Kolom yg ada: {headers}")
        return override
    low = {h: h.lower() for h in headers}
    for h, hl in low.items():
        if any(k in hl for k in keywords):
            return h
    raise SystemExit(f"Gagal deteksi otomatis kolom (cari {keywords}) di header: {headers}. "
                      f"Pakai --nim-col / --prodi-col utk tentukan manual.")


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

    src_id, src_gid = sheet_id_and_gid(args.source)
    src_sh = gc.open_by_key(src_id)
    src_ws = src_sh.get_worksheet_by_id(src_gid) if src_gid is not None else src_sh.sheet1
    print(f"Sumber: '{src_sh.title}' / tab '{src_ws.title}'", file=sys.stderr)

    rows = src_ws.get_all_records()
    if not rows:
        raise SystemExit("Sheet sumber kosong (atau baris 1 bukan header).")
    headers = list(rows[0].keys())
    nim_col = pick_column(headers, ["nim", "npm"], args.nim_col)
    prodi_col = pick_column(headers, ["prodi", "program studi"], args.prodi_col)
    print(f"Kolom terdeteksi -- NIM: '{nim_col}', Prodi: '{prodi_col}'", file=sys.stderr)

    # prodi -> panjang digit -> {"count": int, "examples": [str,...]}
    profile: dict[str, dict[int, dict]] = defaultdict(lambda: defaultdict(lambda: {"count": 0, "examples": []}))
    overall: dict[int, int] = defaultdict(int)
    skipped_blank = 0

    for r in rows:
        nim_raw = str(r.get(nim_col, "") or "").strip()
        prodi = str(r.get(prodi_col, "") or "").strip() or "(tanpa prodi)"
        digits = re.sub(r"\D", "", nim_raw)
        if not digits:
            skipped_blank += 1
            continue
        length = len(digits)
        bucket = profile[prodi][length]
        bucket["count"] += 1
        if len(bucket["examples"]) < 3:
            bucket["examples"].append(nim_raw)
        overall[length] += 1

    print(f"Total baris diproses: {len(rows)} (dilewati krn NIM kosong: {skipped_blank})", file=sys.stderr)
    print("Ringkasan keseluruhan panjang digit:", dict(sorted(overall.items())), file=sys.stderr)

    # susun baris output: per prodi, per panjang digit (urut prodi asc, panjang digit desc jumlah terbanyak dulu)
    out_rows = [["Program Studi", "Panjang Digit NIM", "Jumlah", "Contoh NIM", "Catatan"]]
    for prodi in sorted(profile):
        lengths = profile[prodi]
        multi = len(lengths) > 1
        for length in sorted(lengths, key=lambda l: -lengths[l]["count"]):
            b = lengths[length]
            note = "⚠ Prodi ini punya >1 panjang digit NIM -- cek manual" if multi else ""
            out_rows.append([prodi, length, b["count"], ", ".join(b["examples"]), note])

    out_rows.append([])
    out_rows.append(["== Ringkasan keseluruhan =="])
    out_rows.append(["Panjang Digit NIM", "Total"])
    for length in sorted(overall):
        out_rows.append([length, overall[length]])

    out_id, _ = sheet_id_and_gid(args.output)
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
