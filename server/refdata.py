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
    """[{kelas, prodi}] terurut abjad."""
    out = []
    for r in _rows(KELAS_FILE):
        if len(r) >= 2 and r[0].strip() and r[1].strip():
            out.append({"kelas": r[0].strip(), "prodi": r[1].strip()})
    return sorted(out, key=lambda x: x["kelas"].lower())


def prodi_list():
    return sorted({k["prodi"] for k in kelas()}, key=str.lower)


def pengawas():
    """Pengawas mahasiswa (terurut abjad) lalu dosen di paling bawah, dari tabel DB `pengawas` (server/db.py
    Pengawas) -- TANPA cache proses (beda dgn kelas() di atas yg berkas statis), supaya baris yg ditambah
    admin lewat psql langsung muncul di FE tanpa perlu restart server. nim kosong = dosen. HP kosong/tidak
    valid = pengawas mengisi sendiri (lihat needs_hp di server/main.py /api/meta)."""
    from server.db import Pengawas, SessionLocal
    try:
        with SessionLocal() as db:
            rows = list(db.query(Pengawas).order_by(Pengawas.nama))
    except Exception:  # noqa: BLE001 -- tabel blm ada (DB lama blm dimigrasi) tak boleh mematikan /api/meta
        return []
    mhs, dsn = [], []
    for p in rows:
        item = {"id": p.id, "nama": p.nama, "nim": p.nim, "hp": norm_hp(p.hp) or "", "dosen": not p.nim}
        (dsn if item["dosen"] else mhs).append(item)
    key = lambda x: x["nama"].lower()  # noqa: E731
    return sorted(mhs, key=key) + sorted(dsn, key=key)


def pengawas_by_id(pid: str):
    return next((p for p in pengawas() if p["id"] == pid), None)


def kelas_prodi(nama_kelas: str):
    return next((k["prodi"] for k in kelas() if k["kelas"] == nama_kelas), None)
