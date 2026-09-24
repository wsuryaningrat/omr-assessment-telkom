"""Data rujukan: daftar kelas->prodi (berkas repo) dan daftar pengawas (berkas rahasia, tidak masuk git)."""
import hashlib
import os
import re
from functools import lru_cache

_DIR = os.path.dirname(__file__)
KELAS_FILE = os.environ.get("KELAS_FILE", os.path.join(_DIR, "ref", "kelas.tsv"))
PENGAWAS_FILE = os.environ.get("PENGAWAS_FILE", "/run/secrets/pengawas.tsv")
_FALLBACK = os.path.join(_DIR, "..", "data", "pengawas.tsv")


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


@lru_cache(maxsize=1)
def pengawas():
    """Pengawas mahasiswa (terurut abjad) lalu dosen di paling bawah. HP kosong/tidak valid = pengawas mengisi sendiri."""
    path = PENGAWAS_FILE if os.path.exists(PENGAWAS_FILE) else _FALLBACK
    if not os.path.exists(path):
        return []
    mhs, dsn = [], []
    for r in _rows(path):
        r += [""] * (3 - len(r))
        nama, nim, hp = r[0].strip(), r[1].strip(), r[2].strip()
        if not nama:
            continue
        item = {"id": hashlib.sha1((nama + nim).encode()).hexdigest()[:10], "nama": nama, "nim": nim,
                "hp": norm_hp(hp) or "", "dosen": not nim}
        (dsn if item["dosen"] else mhs).append(item)
    key = lambda x: x["nama"].lower()  # noqa: E731
    return sorted(mhs, key=key) + sorted(dsn, key=key)


def pengawas_by_id(pid: str):
    return next((p for p in pengawas() if p["id"] == pid), None)


def kelas_prodi(nama_kelas: str):
    return next((k["prodi"] for k in kelas() if k["kelas"] == nama_kelas), None)
