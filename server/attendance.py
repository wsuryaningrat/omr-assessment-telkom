"""Kolom "Hadir" (tabel Sesi admin & Monitor publik): jumlah mahasiswa bertanda hadir di Google Sheet
presensi -- SATU TAB PER KELAS (nama tab = nilai "Kelas" yang sama persis dengan di aplikasi), kolom D
baris 4-200 berisi status tiap mahasiswa. Meniru rumus yang sudah ada di Sheet itu sendiri:

    =COUNTIF(INDIRECT("'"&C2&"'!D4:D200"), "Hadir") + COUNTIF(INDIRECT("'"&#REF!&"'!D4:D200"), "✓")

(baris kedua formula itu rusak -- #REF! -- tapi maksudnya jelas: dua ejaan "hadir" yang dipakai orang
yang isi presensi, "Hadir" dan "✓", dihitung sbg satu angka.)

Dibaca via endpoint gviz publik (format=csv tak mendukung dipilih per NAMA tab, hanya per gid numerik;
gviz mendukung nama tab langsung) -- TANPA kredensial, sama seperti server/plotting.py membaca jadwal.
Sheet presensi harus dibagikan "siapa saja yang memiliki tautan dapat melihat". Cache per-kelas dgn TTL
pendek (ATTENDANCE_TTL_S) supaya polling tabel Sesi/Monitor (tiap beberapa detik, bisa puluhan kelas
sekaligus) tak membanjiri Google Sheets dengan request berulang untuk kelas yang sama."""
import csv
import io
import logging
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from server import config

_log = logging.getLogger("attendance")
_LOCK = threading.Lock()
_cache: dict[str, tuple[int | None, float]] = {}   # kelas -> (hadir atau None jika gagal, fetched_at)
_HADIR_VALUES = {"hadir", "✓"}


def _fetch_one(kelas: str) -> int | None:
    sid = config.ATTENDANCE_SHEET_ID
    if not sid or not kelas:
        return None
    url = (f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq"
           f"?tqx=out:csv&sheet={urllib.parse.quote(kelas)}&range={config.ATTENDANCE_RANGE}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mathcenter-ljk/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310 -- URL dari konfigurasi server
            raw = r.read()
        text = raw.decode("utf-8-sig")
        # gviz membalas HTML (bukan CSV) & tetap HTTP 200 bila nama tab tak ada -- deteksi dari isi,
        # bukan status code, supaya kelas yg belum punya tab presensi dapat "-" (bukan exception berisik).
        if "<html" in text[:200].lower():
            return None
        n = sum(1 for row in csv.reader(io.StringIO(text)) for cell in row if cell.strip().lower() in _HADIR_VALUES)
        return n
    except Exception as e:  # noqa: BLE001 -- kolom tambahan, tak boleh menggagalkan tabel Sesi/Monitor
        _log.info("gagal ambil presensi kelas %r: %s", kelas, e)
        return None


def hadir_counts(kelas_list) -> dict[str, int | None]:
    """{kelas: jumlah hadir (None bila tab tak ada/gagal diambil)} utk SEKUMPULAN kelas sekaligus --
    dipakai GET /api/admin/sessions & /api/monitor-sesi. Kelas yg cache-nya masih segar tak diambil
    ulang; sisanya diambil PARALEL (satu per kelas, request kecil) supaya daftar dgn banyak kelas
    berbeda tak menunggu Google Sheets satu-satu secara berurutan."""
    if not config.ATTENDANCE_SHEET_ID:
        return {}
    wanted = sorted({k for k in kelas_list if k})
    now = time.time()
    out = {}
    stale = []
    with _LOCK:
        for k in wanted:
            cached = _cache.get(k)
            if cached is not None and now - cached[1] < config.ATTENDANCE_TTL_S:
                out[k] = cached[0]
            else:
                stale.append(k)
    if stale:
        with ThreadPoolExecutor(max_workers=min(8, len(stale))) as ex:
            results = dict(zip(stale, ex.map(_fetch_one, stale)))
        with _LOCK:
            for k, n in results.items():
                _cache[k] = (n, now)
        out.update(results)
    return out
