"""Jadwal plotting pengawas (Google Sheet yang dibagikan lewat tautan) -> daftar slot ujian, dengan cache."""
import csv
import io
import os
import threading
import time
import urllib.request

from server import config

_LOCK = threading.Lock()
_cache = {"rows": None, "at": 0.0, "error": None}
HARI = ["SENIN", "SELASA", "RABU", "KAMIS", "JUMAT", "SABTU", "MINGGU"]


def _cache_file():
    return os.path.join(config.UPLOAD_DIR, ".plotting_cache.csv")


def _download() -> str:
    req = urllib.request.Request(config.PLOTTING_CSV_URL, headers={"User-Agent": "mathcenter-ljk/1.0"})
    with urllib.request.urlopen(req, timeout=12) as r:  # noqa: S310 — URL dari konfigurasi server
        raw = r.read()
    text = raw.decode("utf-8-sig")
    if "Kelas" not in text.split("\n", 1)[0]:
        raise ValueError("Isi bukan jadwal plotting (kemungkinan tautan tidak lagi publik)")
    return text


def _parse(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        kelas = (r.get("Kelas") or "").strip()
        hari = (r.get("Hari") or "").strip().upper()
        if not kelas or not hari:
            continue
        jml = (r.get("Jml Mahasiswa") or "").strip()
        rows.append({
            "no": (r.get("No") or "").strip(), "hari": hari,
            "jam": f"{(r.get('Jam Mulai') or '').strip()}–{(r.get('Jam Selesai') or '').strip()}".strip("–"),
            "jam_mulai": (r.get("Jam Mulai") or "").strip(),
            "gedung": (r.get("Gedung") or "").strip(), "ruangan": (r.get("Ruangan") or "").strip(),
            "kelas": kelas, "prodi": (r.get("Prodi") or "").strip(),
            "jml_mhs": int(jml) if jml.isdigit() else 0,
            "pengawas": (r.get("Nama Pengawas") or "").strip(), "mode": (r.get("Mode") or "").strip(),
        })
    return rows


def schedule(force: bool = False):
    """Kembalikan (rows, fetched_at_epoch, error). Bila unduhan gagal, dipakai data terakhir yang baik (memori lalu berkas)."""
    now = time.time()
    with _LOCK:
        if not force and _cache["rows"] is not None and now - _cache["at"] < config.PLOTTING_TTL_S:
            return _cache["rows"], _cache["at"], _cache["error"]
        try:
            text = _download()
            rows = _parse(text)
            if not rows:
                raise ValueError("Jadwal kosong")
            _cache.update(rows=rows, at=now, error=None)
            try:
                with open(_cache_file(), "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass
        except Exception as e:  # noqa: BLE001 — jaringan/tautan bermasalah: pakai cadangan
            _cache["error"] = f"{type(e).__name__}: {str(e)[:160]}"
            if _cache["rows"] is None:
                try:
                    with open(_cache_file(), encoding="utf-8") as f:
                        _cache["rows"] = _parse(f.read())
                        _cache["at"] = os.path.getmtime(_cache_file())
                except OSError:
                    _cache["rows"] = []
        return _cache["rows"], _cache["at"], _cache["error"]
