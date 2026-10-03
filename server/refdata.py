"""Data rujukan: daftar kelas->prodi (berkas repo) dan daftar pengawas (tabel DB `pengawas`, lihat
server/db.py Pengawas -- bisa ditambah admin langsung lewat psql, tanpa redeploy)."""
import os
import re
from functools import lru_cache

_DIR = os.path.dirname(__file__)
KELAS_FILE = os.environ.get("KELAS_FILE", os.path.join(_DIR, "ref", "kelas.tsv"))


def _rows(path):
    with open(path, encoding="utf-8-sig") as f:
        lines = [ln.rstrip("\r\n") for ln in f if ln.strip()]
    return [ln.split("\t") for ln in lines[1:]]


def norm_hp(raw: str):
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("62"):
        d = d[2:]
    d = d.lstrip("0")
    return "+62" + d if re.fullmatch(r"8\d{8,11}", d) else None


@lru_cache(maxsize=1)
def kelas():
    """[{kelas, prodi, fakultas}] terurut abjad. Fakultas = kolom ke-3 kelas.tsv (kosong bila belum dipetakan)."""
    out = []
    for r in _rows(KELAS_FILE):
        if len(r) >= 2 and r[0].strip() and r[1].strip():
            out.append({"kelas": r[0].strip(), "prodi": r[1].strip(), "fakultas": r[2].strip() if len(r) > 2 else ""})
    return sorted(out, key=lambda x: x["kelas"].lower())


def kelas_info(nama_kelas: str):
    """Baris tabel kelas utk nama kelas persis (tanpa beda kapital), atau None -- sumber prodi & fakultas
    sesi: pengawas cukup memilih kelas, server yg mengisi sisanya (lihat main._resolve_identity)."""
    n = (nama_kelas or "").strip().lower()
    return next((k for k in kelas() if k["kelas"].lower() == n), None)


def prodi_list():
    return sorted({k["prodi"] for k in kelas()}, key=str.lower)


# Gelar akademik umum yg menandakan nama ybs DOSEN -- dipakai HANYA saat nim kosong (lihat pengawas()
# di bawah). Ditemukan 2 Okt 2026: banyak baris pengawas MAHASISWA peninggalan migrasi pengawas.tsv lama
# (sebelum kolom nim ada) & hasil "Lainnya -- isi sendiri" (lihat main.py _sync_pengawas_contact, yg
# selalu nim="") ikut bernim kosong -- nim-kosong SENDIRIAN tak cukup jadi penanda dosen, jadi dicek jg
# apakah namanya memuat gelar (mis. "Dr. ..." atau "..., S.Si., M.Stat."), krn SEMUA baris dosen asli di
# data saat ini memuat gelar spt itu sedangkan baris mahasiswa tidak pernah.
_DOSEN_TITLE_RE = re.compile(r"(^|\s)(Dr|Prof|Ir|Drs|Dra)\.(\s|$)|,\s*[A-Za-z]+\.")


def _looks_like_dosen(nama: str) -> bool:
    return bool(_DOSEN_TITLE_RE.search(nama or ""))


def pengawas():
    """Pengawas mahasiswa (terurut abjad) lalu dosen di paling bawah, dari tabel DB `pengawas` (server/db.py
    Pengawas) -- TANPA cache proses (beda dgn kelas() di atas yg berkas statis), supaya baris yg ditambah
    admin lewat psql langsung muncul di FE tanpa perlu restart server. Dosen = nim kosong DAN namanya
    memuat gelar akademik (lihat _looks_like_dosen) -- nim kosong tanpa gelar dianggap mahasiswa (lihat
    catatan di _DOSEN_TITLE_RE). HP kosong/tidak valid = pengawas mengisi sendiri (lihat needs_hp di
    server/main.py /api/meta) -- TERMASUK mahasiswa yg nim-nya kebetulan belum tercatat di tabel ini."""
    from server.db import Pengawas, SessionLocal
    try:
        with SessionLocal() as db:
            rows = list(db.query(Pengawas).order_by(Pengawas.nama))
    except Exception:  # noqa: BLE001 -- tabel blm ada (DB lama blm dimigrasi) tak boleh mematikan /api/meta
        return []
    mhs, dsn = [], []
    for p in rows:
        is_dosen = not p.nim and _looks_like_dosen(p.nama)
        item = {"id": p.id, "nama": p.nama, "nim": p.nim, "hp": norm_hp(p.hp) or "", "dosen": is_dosen}
        (dsn if item["dosen"] else mhs).append(item)
    key = lambda x: x["nama"].lower()  # noqa: E731
    return sorted(mhs, key=key) + sorted(dsn, key=key)


def pengawas_by_id(pid: str):
    return next((p for p in pengawas() if p["id"] == pid), None)


def kelas_prodi(nama_kelas: str):
    return next((k["prodi"] for k in kelas() if k["kelas"] == nama_kelas), None)
