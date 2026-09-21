"""Pekerjaan pemindaian — dijalankan di proses terpisah (ProcessPoolExecutor).

Fungsi di sini murni: baca berkas dari disk, pindai, kembalikan hasil sebagai dict.
Penulisan ke database dilakukan oleh proses utama.
"""
import os

_TEMPLATE = None


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


def init_worker(parent_pid):
    """Pekerja ikut berhenti bila proses server (induk) mati — tanpa ini pekerja menjadi yatim dan menghabiskan RAM
    setiap kali server di-restart/--reload atau dimatikan paksa."""
    import threading
    import time

    def _watch():
        while True:
            time.sleep(2)
            if os.getppid() != parent_pid:
                os._exit(0)

    threading.Thread(target=_watch, daemon=True, name="parent-watch").start()


def warmup(_=None):
    _template()
    return os.getpid()
