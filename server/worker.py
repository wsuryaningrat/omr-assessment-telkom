"""Pekerjaan pemindaian — dijalankan di proses terpisah (ProcessPoolExecutor).

Fungsi di sini murni: baca berkas dari disk, pindai, kembalikan hasil sebagai dict.
Penulisan ke database dilakukan oleh proses utama.
"""
import os

_TEMPLATE = None
_PENDING = None    # multiprocessing.Value: jumlah pekerjaan pindai yang sedang berjalan + mengantre (dari proses utama)


def threads_for(pending, workers, adaptive=True, floor=1):
    """Jumlah thread OpenCV untuk satu foto. Antrean penuh -> 1 thread (tiap core sudah dipakai satu worker);
    antrean sepi -> core yang menganggur dipakai thread agar satu foto lebih cepat. Hasil bacaan tidak berubah."""
    if not adaptive:
        return max(1, floor)
    return max(1, floor, workers // max(1, pending))


class PathUpload:
    """Adaptor berkas di disk agar cocok dengan iter_images_from_file()."""

    def __init__(self, path, name):
        self.name = name
        self._path = path

    def getvalue(self):
        with open(self._path, "rb") as f:
            return f.read()


def _template():
    global _TEMPLATE
    if _TEMPLATE is None:
        from scanner.service import load_default_template
        _TEMPLATE = load_default_template()
    return _TEMPLATE


def _int_keys(kunci):
    return {name: {int(q): a for q, a in data.items()} for name, data in kunci.items()}


def scan_file(path, name, pengawas, kunci, only_page=None, with_overlay=False):
    """Pindai satu berkas (semua halaman, atau `only_page`).

    Mengembalikan list dict: {page, doc_name, record, status, overlay_jpeg?}.
    """
    import cv2
    from core.pdf_utils import iter_images_from_file
    from scanner.service import SCAN_MAX_SIDE, scan_page

    tpl = _template()
    if not tpl:
        raise RuntimeError("Template pemindai tidak ditemukan")
    _set_threads()
    k_cache = _int_keys(kunci)
    info = {"hp": pengawas["hp"], "ruangan": pengawas["ruangan"], "prodi": pengawas["prodi"], "kelas": pengawas.get("kelas", "")}
    out = []
    for page, (doc_name, img_bgr) in enumerate(iter_images_from_file(PathUpload(path, name), target_dpi=200, max_side=SCAN_MAX_SIDE)):
        if only_page is not None and page != only_page:
            continue
        rec, prev = scan_page(img_bgr, doc_name, tpl, pengawas["fakultas"], pengawas["nama"], k_cache, info, with_overlay=with_overlay)
        item = {"page": page, "doc_name": doc_name, "record": rec, "status": prev["status"]}
        if with_overlay and prev.get("overlay") is not None:
            ok, buf = cv2.imencode(".jpg", prev["overlay"], [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            item["overlay_jpeg"] = buf.tobytes()
        out.append(item)
        del img_bgr, prev
    return out


def _set_threads():
    import cv2
    from server import config
    n = threads_for(_PENDING.value if _PENDING is not None else config.SCAN_WORKERS, config.SCAN_WORKERS,
                    os.environ.get("SCAN_ADAPTIVE_THREADS", "1") == "1", int(os.environ.get("SCAN_CV_THREADS", "1")))
    cv2.setNumThreads(n)


def init_worker(parent_pid, pending=None):
    """Pekerja ikut berhenti bila proses server (induk) mati — tanpa ini pekerja menjadi yatim dan menghabiskan RAM
    setiap kali server di-restart/--reload atau dimatikan paksa."""
    import threading
    import time

    global _PENDING
    _PENDING = pending

    # Pemindaian menghabiskan CPU. Prioritas lebih rendah (nice) agar proses API (polling status, upload) tetap
    # responsif saat semua core dipakai memindai. Ubah/matikan lewat SCAN_NICE (0 = normal).
    try:
        os.nice(int(os.environ.get("SCAN_NICE", "10")))
    except (OSError, ValueError, AttributeError):
        pass

    # PENTING: satu thread OpenCV per worker. Default OpenCV memakai semua core di TIAP proses; dengan process pool
    # itu membuat thread saling berebut core (oversubscription): diukur di VPS 4 vCPU, 4 worker x 4 thread hanya
    # 0,43 foto/dtk, sedangkan 4 worker x 1 thread 0,58 foto/dtk (skala hampir linear). Hasil pembacaan identik.
    try:
        import cv2
        cv2.setNumThreads(int(os.environ.get("SCAN_CV_THREADS", "1")))
    except Exception:  # noqa: BLE001
        pass

    def _watch():
        while True:
            time.sleep(2)
            if os.getppid() != parent_pid:
                os._exit(0)

    threading.Thread(target=_watch, daemon=True, name="parent-watch").start()


def warmup(_=None):
    _template()
    return os.getpid()
