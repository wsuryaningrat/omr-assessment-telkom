"""Logika murni profil panjang digit NIM per program studi -- dipakai server/admin.py (endpoint
GET /api/admin/nim-profile, baca langsung dari sheet referensi) & tools/nim_profile.py (skrip CLI,
tulis ke sheet output). Tak sentuh jaringan/Google Sheets sama sekali di sini -- cuma olah data `rows`
yg sudah dibaca pemanggil, supaya gampang diuji & dipakai ulang di dua tempat tanpa duplikasi."""
import re
from collections import defaultdict
from urllib.parse import parse_qs, urlparse


def parse_sheet_url(url: str):
    """URL Google Sheet -> (sheet_id, gid|None)."""
    parts = urlparse(url)
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", parts.path)
    if not m:
        raise ValueError(f"URL spreadsheet tak dikenali: {url}")
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


def pick_column(headers: list, keywords: list, override: str | None = None) -> str:
    if override:
        if override not in headers:
            raise ValueError(f"Kolom '{override}' tak ditemukan. Kolom yg ada: {headers}")
        return override
    low = {h: h.lower() for h in headers}
    for h, hl in low.items():
        if any(k in hl for k in keywords):
            return h
    raise ValueError(f"Gagal deteksi otomatis kolom (cari {keywords}) di header: {headers}. "
                      f"Tentukan manual (nim_col/prodi_col).")


def profile_nim_lengths(rows: list[dict], nim_col: str | None = None, prodi_col: str | None = None) -> dict:
    """`rows`: list of dict spt gspread Worksheet.get_all_records() (baris 1 = header). Kembalikan
    struktur siap-tampil: kolom yg kepakai, ringkasan keseluruhan per panjang digit, & rincian per
    prodi (tiap panjang digit yg muncul, jumlah, contoh NIM, tanda kalau prodi itu py >1 panjang digit
    -- itulah kandidat yg perlu dicek manual sblm NIM pada lembar tervalidasi dikoreksi)."""
    if not rows:
        raise ValueError("Data kosong (sheet kosong atau baris 1 bukan header).")
    headers = list(rows[0].keys())
    nim_col = pick_column(headers, ["nim", "npm"], nim_col)
    prodi_col = pick_column(headers, ["prodi", "program studi"], prodi_col)

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

    by_prodi = []
    for prodi in sorted(profile):
        lengths = profile[prodi]
        multi = len(lengths) > 1
        items = []
        for length in sorted(lengths, key=lambda l: -lengths[l]["count"]):
            b = lengths[length]
            items.append({"length": length, "count": b["count"], "examples": b["examples"],
                          "flagged": multi})
        by_prodi.append({"prodi": prodi, "multi_length": multi, "lengths": items})

    return {
        "columns": {"nim": nim_col, "prodi": prodi_col},
        "total_rows": len(rows), "skipped_blank": skipped_blank,
        "overall": [{"length": length, "count": overall[length]} for length in sorted(overall)],
        "by_prodi": by_prodi,
        "flagged_prodi_count": sum(1 for p in by_prodi if p["multi_length"]),
    }


def profile_nim_by_kelas(rows: list[dict], nim_col: str | None = None, kelas_col: str | None = None,
                          prefix_len: int = 2) -> dict:
    """Spt profile_nim_lengths, tapi dikelompokkan per KELAS (bukan prodi) & tiap kelompok dipecah lagi
    per (jumlah digit, N digit awal) -- dipakai tools/nim_profile_by_kelas.py. Satu kelas idealnya py SATU
    kombinasi (jumlah digit, digit awal) yg konsisten (NIM-nya satu angkatan/prodi yg sama); kelas yg punya
    >1 kombinasi artinya ada NIM nyasar/salah format di kelas itu -- patut dicek manual."""
    if not rows:
        raise ValueError("Data kosong (sheet kosong atau baris 1 bukan header).")
    headers = list(rows[0].keys())
    nim_col = pick_column(headers, ["nim", "npm"], nim_col)
    kelas_col = pick_column(headers, ["kelas"], kelas_col)

    # kelas -> (panjang digit, N digit awal) -> {"count": int, "examples": [str,...]}
    profile: dict[str, dict[tuple, dict]] = defaultdict(lambda: defaultdict(lambda: {"count": 0, "examples": []}))
    skipped_blank = 0

    for r in rows:
        nim_raw = str(r.get(nim_col, "") or "").strip()
        kelas = str(r.get(kelas_col, "") or "").strip() or "(tanpa kelas)"
        digits = re.sub(r"\D", "", nim_raw)
        if not digits:
            skipped_blank += 1
            continue
        key = (len(digits), digits[:prefix_len])
        bucket = profile[kelas][key]
        bucket["count"] += 1
        if len(bucket["examples"]) < 3:
            bucket["examples"].append(nim_raw)

    by_kelas = []
    for kelas in sorted(profile):
        combos = profile[kelas]
        multi = len(combos) > 1
        items = []
        for (length, prefix) in sorted(combos, key=lambda k: -combos[k]["count"]):
            b = combos[(length, prefix)]
            items.append({"length": length, "prefix": prefix, "count": b["count"],
                          "examples": b["examples"], "flagged": multi})
        by_kelas.append({"kelas": kelas, "multi_combo": multi, "items": items})

    return {
        "columns": {"nim": nim_col, "kelas": kelas_col}, "prefix_len": prefix_len,
        "total_rows": len(rows), "skipped_blank": skipped_blank,
        "by_kelas": by_kelas,
        "flagged_kelas_count": sum(1 for k in by_kelas if k["multi_combo"]),
    }
