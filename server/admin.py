"""Endpoint admin (header X-Admin-Token): ringkasan, sesi, kunci jawaban, sinkron Google Sheet, ekspor."""
import csv
import datetime as dt
import io
import os
import uuid

import cv2
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile as FUploadFile
from fastapi.responses import Response
from sqlalchemy import Integer, func, select

from core.evaluator import parse_kunci_jawaban_raw_rows
from core.pdf_utils import iter_images_from_file
from scanner.service import classify_scan_status
from server import auth, config, plotting, services, sheets
from server.db import Kunci, ScanSession, Sheet, SessionLocal, UploadFile


def get_db():
    with SessionLocal() as db:
        yield db


router = APIRouter(prefix="/api/admin", dependencies=[Depends(auth.require_admin)])


@router.get("/summary")
def summary(db=Depends(get_db)):
    q = lambda *c: db.scalar(select(func.count()).select_from(ScanSession).where(*c)) or 0  # noqa: E731
    return {
        "sessions": q(),
        "submitted": q(ScanSession.submitted.is_(True)),
        "open": q(ScanSession.submitted.is_(False)),
        "sheets_submitted": db.scalar(select(func.count()).select_from(Sheet).join(ScanSession).where(ScanSession.submitted.is_(True))) or 0,
        "unsynced": q(ScanSession.submitted.is_(True), ScanSession.synced_at.is_(None)),
        "sync_failed": q(ScanSession.submitted.is_(True), ScanSession.synced_at.is_(None), ScanSession.sync_attempts >= config.SYNC_MAX_ATTEMPTS),
        "kunci": db.scalar(select(func.count()).select_from(Kunci)) or 0,
        "plot_url": config.PLOTTING_SHEET_URL,
        "gsheet_configured": sheets.get_client() is not None,
        "gsheet_url": (getattr(sheets.get_client(), "url", None) or config.GSHEET_URL or ""),
        "state": services.STATE,
    }


@router.get("/monitor")
def monitor(mode: str = "onsite", refresh: bool = False, db=Depends(get_db)):
    """Progres unggah per hari terhadap jadwal plotting (default: hanya tes onsite)."""
    rows, fetched, perr = plotting.schedule(force=refresh)
    want = mode.strip().lower()
    slots_src = [r for r in rows if not want or want == "semua" or r["mode"].lower() == want]

    cond = []
    if config.MONITOR_SINCE:
        cond.append(ScanSession.created_at >= dt.datetime.fromisoformat(config.MONITOR_SINCE.replace("Z", "+00:00")))
    q = (select(ScanSession.id, ScanSession.kelas, ScanSession.submitted, ScanSession.submitted_at, ScanSession.created_at,
                ScanSession.nama_pengawas, func.count(Sheet.id), func.coalesce(func.sum(func.cast(Sheet.validated, Integer)), 0))
         .outerjoin(Sheet, Sheet.session_id == ScanSession.id).where(*cond).group_by(ScanSession.id))
    by_kelas = {}
    for sid, kelas, sub, sub_at, cr_at, nama, n_sheet, n_val in db.execute(q):
        by_kelas.setdefault((kelas or "").strip().lower(), []).append(
            {"id": sid, "kelas": kelas, "submitted": bool(sub), "submitted_at": sub_at, "created_at": cr_at,
             "pengawas": nama, "lembar": int(n_sheet or 0), "validated": int(n_val or 0)})

    days, seen = {}, set()
    for r in slots_src:
        key = r["kelas"].strip().lower(); seen.add(key)
        ss = by_kelas.get(key, [])
        done = [x for x in ss if x["submitted"]]
        openx = [x for x in ss if not x["submitted"]]
        if done:
            status, lembar = "selesai", sum(x["lembar"] for x in done)
            at = max((x["submitted_at"] for x in done if x["submitted_at"]), default=None)
            oleh = done[-1]["pengawas"]
        elif openx:
            status, lembar, at, oleh = "berjalan", sum(x["lembar"] for x in openx), None, openx[-1]["pengawas"]
        else:
            status, lembar, at, oleh = "belum", 0, None, ""
        d = days.setdefault(r["hari"], {"hari": r["hari"], "slots": []})
        d["slots"].append({**r, "status": status, "lembar": lembar, "submitted_at": at.isoformat() if at else None,
                           "oleh": oleh, "beda_pengawas": bool(oleh) and oleh.strip().lower() != r["pengawas"].strip().lower()})

    out_days = []
    for name in sorted(days, key=lambda h: plotting.HARI.index(h) if h in plotting.HARI else 99):
        d = days[name]; sl = sorted(d["slots"], key=lambda x: (x["jam_mulai"], x["gedung"], x["kelas"]))
        tot = len(sl); selesai = sum(x["status"] == "selesai" for x in sl); jalan = sum(x["status"] == "berjalan" for x in sl)
        mhs = sum(x["jml_mhs"] for x in sl)
        up = sum(min(x["lembar"], x["jml_mhs"]) for x in sl if x["status"] == "selesai")
        prog = sum(min(x["lembar"], x["jml_mhs"]) for x in sl if x["status"] == "berjalan")
        out_days.append({"hari": name, "total": tot, "selesai": selesai, "berjalan": jalan, "belum": tot - selesai - jalan,
                         "pct_kelas": round(selesai / tot * 100, 1) if tot else 0, "mhs_total": mhs, "mhs_upload": up, "mhs_proses": prog,
                         "pct_mhs": round(up / mhs * 100, 1) if mhs else 0, "slots": sl})
    tot = sum(d["total"] for d in out_days); selesai = sum(d["selesai"] for d in out_days)
    mhs = sum(d["mhs_total"] for d in out_days); up = sum(d["mhs_upload"] for d in out_days)
    extra = [{"kelas": x["kelas"], "pengawas": x["pengawas"], "lembar": x["lembar"], "submitted": x["submitted"],
              "submitted_at": x["submitted_at"].isoformat() if x["submitted_at"] else None}
             for k, xs in by_kelas.items() if k not in seen for x in xs][:60]
    wib = dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))
    return {"mode": want or "semua", "hari_ini": plotting.HARI[wib.weekday()], "since": config.MONITOR_SINCE or None,
            "plotting_at": dt.datetime.fromtimestamp(fetched, dt.timezone.utc).isoformat() if fetched else None, "plotting_error": perr,
            "plot_url": config.PLOTTING_SHEET_URL,
            "overall": {"total": tot, "selesai": selesai, "berjalan": sum(d["berjalan"] for d in out_days),
                        "pct_kelas": round(selesai / tot * 100, 1) if tot else 0, "mhs_total": mhs, "mhs_upload": up,
                        "pct_mhs": round(up / mhs * 100, 1) if mhs else 0},
            "days": out_days, "di_luar_jadwal": extra}


def _jml_mhs_by_kelas():
    """{kelas (lower): jumlah mahasiswa terjadwal} dari jadwal plotting -- best effort, jangan sampai
    laman admin gagal total hanya krn Google Sheet jadwal belum/tak terhubung."""
    try:
        rows, _fetched, _perr = plotting.schedule()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in rows:
        k = (r.get("kelas") or "").strip().lower()
        if k:
            out[k] = r.get("jml_mhs", 0)
    return out


def _session_status(s: ScanSession) -> str:
    """scanning (masih ada berkas diproses) -> perlu_cek (selesai discan, belum tervalidasi admin) ->
    validated (admin sudah menandai selesai dicek). Terpisah dari validasi per-lembar pengawas
    (Sheet.validated) & submit pengawas (ScanSession.submitted) -- lihat _session_row."""
    if any(f.state in ("queued", "processing") for f in s.files):
        return "scanning"
    if s.admin_validated:
        return "validated"
    return "perlu_cek"


def _session_row(db, s: ScanSession, jml_mhs_map: dict) -> dict:
    n_files = len(s.files)
    n_pending = sum(1 for f in s.files if f.state in ("queued", "processing"))
    n_failed = sum(1 for f in s.files if f.state == "failed")
    n_orphans = len(_orphan_files(db, s))
    # "dibersihkan" hanya berarti sesuatu bila sesi PERNAH punya berkas -- sesi baru yg belum punya berkas
    # sama sekali tidak dianggap "sudah dibersihkan". Foto kini dibagi per Fakultas/Prodi/Kelas (lihat
    # server/main.py _class_folder), jadi dicek per BERKAS milik sesi ini, bukan per folder.
    photos_cleared = n_files > 0 and not services.session_has_photos(s)
    return {
        "id": s.id, "nama": s.nama_pengawas, "hp": s.hp, "kelas": s.kelas,
        "prodi": s.prodi, "fakultas": s.fakultas, "lembar": len(s.sheets),
        "kode_soal": s.kode_soal, "hari_ujian": s.hari_ujian,
        "jml_mhs": jml_mhs_map.get((s.kelas or "").strip().lower()),
        "validated": sum(1 for x in s.sheets if x.validated), "submitted": s.submitted,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        "synced": s.synced_at is not None, "sync_attempts": s.sync_attempts or 0, "sync_error": s.sync_error,
        "files": {"total": n_files, "pending": n_pending, "failed": n_failed, "orphans": n_orphans},
        "status": _session_status(s),
        "admin_validated": s.admin_validated,
        "admin_validated_at": s.admin_validated_at.isoformat() if s.admin_validated_at else None,
        "photos_cleared": photos_cleared,
    }


_DERIVED_STATUSES = ("scanning", "perlu_cek", "validated")


@router.get("/sessions")
def sessions(page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100), q: str = "", status: str = "all", db=Depends(get_db)):
    cond = []
    if status == "submitted":
        cond.append(ScanSession.submitted.is_(True))
    elif status == "open":
        cond.append(ScanSession.submitted.is_(False))
    elif status == "unsynced":
        cond += [ScanSession.submitted.is_(True), ScanSession.synced_at.is_(None)]
    if q.strip():
        like = f"%{q.strip()}%"
        cond.append(ScanSession.nama_pengawas.ilike(like) | ScanSession.kelas.ilike(like) | ScanSession.prodi.ilike(like))
    jml_mhs_map = _jml_mhs_by_kelas()
    if status in _DERIVED_STATUSES:
        # scanning/perlu_cek/validated butuh f.state per berkas & s.admin_validated -- tak bisa disaring lewat
        # SQL LIMIT/OFFSET tanpa join rumit, jadi disaring+dipaginasi di Python. Jumlah sesi total (bukan per
        # kelas ujian) di alat ini kecil, jadi memuat semua baris yg cocok `q` sekali lalu menyaring aman.
        all_rows = db.scalars(select(ScanSession).where(*cond).order_by(ScanSession.created_at.desc()))
        items_all = [_session_row(db, s, jml_mhs_map) for s in all_rows if _session_status(s) == status]
        total = len(items_all)
        items = items_all[(page - 1) * size: (page - 1) * size + size]
    else:
        total = db.scalar(select(func.count()).select_from(ScanSession).where(*cond)) or 0
        rows = db.scalars(select(ScanSession).where(*cond).order_by(ScanSession.created_at.desc()).offset((page - 1) * size).limit(size))
        items = [_session_row(db, s, jml_mhs_map) for s in rows]
    return {"total": total, "page": page, "size": size, "items": items}


def _admin_session_or_404(db, sid: str) -> ScanSession:
    s = db.get(ScanSession, sid)
    if s is None:
        raise HTTPException(404, "Sesi tidak ditemukan")
    return s


@router.get("/sessions/{sid}/sheets")
def admin_session_sheets(sid: str, db=Depends(get_db)):
    """Detail keterisian tiap lembar (mahasiswa) dlm satu sesi -- utk admin mengecek langsung tanpa perlu
    membuka dashboard pengawas. Admin BOLEH lihat nama mahasiswa & nilai (beda dgn dashboard pengawas yg
    sengaja menyembunyikannya, lihat _sheet_view di server/main.py)."""
    s = _admin_session_or_404(db, sid)
    items = []
    for sh in s.sheets:
        r = sh.record
        up = db.get(UploadFile, sh.file_id)
        items.append({
            "id": sh.id, "seq": sh.seq, "file": r.get("File"), "nama": r.get("Nama Mahasiswa"),
            "npm": r.get("NPM"), "kode_soal": r.get("Kode Soal"), "fakultas_ljk": r.get("Fakultas (LJK)"),
            "terisi": r.get("Jawaban Terisi"), "nilai": r.get("Nilai"),
            "label": classify_scan_status({"status": sh.scan_status}, sh.validated),
            "validated": sh.validated,
            "photo_exists": bool(up and up.path and os.path.exists(up.path)),
        })
    orphans = [{"id": f.id, "name": f.name, "size": f.size} for f in _orphan_files(db, s)]
    return {"items": items, "orphan_files": orphans}


def _orphan_files(db, s: ScanSession):
    """Berkas yg tercatat SELESAI (state='done') tapi TAK MENGHASILKAN lembar sama sekali -- ditemukan lewat
    investigasi 29 Sep 2026: pemindaian lembar sulit bisa makan 30-90 dtk, dan kalau container di-restart
    (deploy/insiden) tepat di tengah itu, prosesnya terputus tanpa sempat tercatat gagal ATAU sukses dgn
    benar -- pengawas/admin tak melihat error apa pun, cuma lembar yg "hilang" diam-diam. Foto sumbernya
    aman (tak terhapus), tinggal diproses ulang lewat admin_reprocess_file."""
    return [f for f in s.files if f.state == "done"
            and not db.scalar(select(func.count()).select_from(Sheet).where(Sheet.file_id == f.id))]


@router.post("/sessions/{sid}/reprocess-orphans")
def admin_reprocess_orphans(sid: str, db=Depends(get_db)):
    """Proses ulang SEMUA berkas 'hilang senyap' (lihat _orphan_files) di sesi ini lewat antrean latar
    belakang biasa -- aman dipanggil berkali-kali; hasil lama (kalau ada, tak ada di sini krn definisinya
    nol lembar) tak tersentuh, cuma menambah lembar baru bila kali ini berhasil."""
    from server import main as _main
    s = _admin_session_or_404(db, sid)
    orphans = _orphan_files(db, s)
    for f in orphans:
        if not f.path or not os.path.exists(f.path):
            continue
        f.state = "queued"
        f.error = None
    db.commit()
    for f in orphans:
        if f.path and os.path.exists(f.path):
            _main._enqueue(f.id)
    return {"ok": True, "diproses_ulang": len(orphans)}


@router.post("/files/{fid}/reprocess")
def admin_reprocess_file(fid: str, db=Depends(get_db)):
    """Proses ulang SATU berkas 'hilang senyap' (state='done', nol lembar -- lihat _orphan_files) lewat
    antrean latar belakang biasa (sama spt unggahan baru), BUKAN sinkron, krn bisa lambat (~1 menit utk foto
    sulit). Kembalikan segera (queued=True); pantau hasilnya lewat GET /sessions/{sid}/sheets sesudahnya."""
    from server import main as _main
    f = db.get(UploadFile, fid)
    if f is None:
        raise HTTPException(404, "Berkas tidak ditemukan")
    if not f.path or not os.path.exists(f.path):
        raise HTTPException(410, "Berkas sumber sudah tak ada")
    f.state = "queued"
    f.error = None
    db.commit()
    _main._enqueue(fid)
    return {"ok": True, "queued": True}


class _BytesUpload:
    """Adaptor kecil supaya core.pdf_utils.iter_images_from_file (dibuat utk objek upload FastAPI/Streamlit)
    bisa dipakai langsung dgn path berkas yg sudah tersimpan di disk."""
    def __init__(self, path):
        self.name = os.path.basename(path)
        self._path = path

    def read(self):
        with open(self._path, "rb") as f:
            return f.read()


@router.get("/sheets/{shid}/photo")
def admin_sheet_photo(shid: str, db=Depends(get_db)):
    """Foto ASLI lembar ini (belum diproses) -- CEPAT: cuma decode gambar (termasuk HEIC/halaman PDF terkait),
    TANPA menjalankan pipeline OMR (deteksi pojok, baca bulatan). Beda dgn GET /api/sheets/{shid}/preview
    (endpoint pengawas) yg menjalankan pemindaian penuh & bisa sampai puluhan detik -- dipakai admin utk
    intip cepat foto sumbernya apa adanya."""
    sh = db.get(Sheet, shid)
    if sh is None:
        raise HTTPException(404, "Lembar tidak ditemukan")
    up = db.get(UploadFile, sh.file_id)
    if not up or not up.path or not os.path.exists(up.path):
        raise HTTPException(410, "Berkas sumber sudah tak ada")
    try:
        for i, (_name, bgr) in enumerate(iter_images_from_file(_BytesUpload(up.path), max_side=1600)):
            if i == sh.page:
                ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ok:
                    raise HTTPException(422, "Gagal membuat pratinjau")
                return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Gagal membaca berkas: {e}")
    raise HTTPException(404, "Halaman tak ditemukan dalam berkas")


@router.post("/sheets/{shid}/rescan")
def admin_rescan_sheet(shid: str, db=Depends(get_db)):
    """Pindai ulang lembar ini dgn foto sumber yg SAMA (tak berubah) -- mis. utk membetulkan hasil baca
    setelah perbaikan kode, tanpa perlu pengawas foto ulang. Admin-only, TIDAK terhalang sesi sudah disubmit
    (beda dgn alur pengawas) -- rekap/nilai ikut diperbarui langsung krn kunci jawaban disertakan saat
    memindai (lihat scan_page)."""
    from server import main as _main   # impor lokal: hindari impor melingkar (main mengimpor admin)
    sh = db.get(Sheet, shid)
    if sh is None:
        raise HTTPException(404, "Lembar tidak ditemukan")
    s = db.get(ScanSession, sh.session_id)
    up = db.get(UploadFile, sh.file_id)
    if not up or not up.path or not os.path.exists(up.path):
        raise HTTPException(410, "Berkas sumber sudah tak ada (mis. sudah 'Bersihkan foto') -- tak bisa dipindai ulang")
    fut = _main._submit_scan(up.path, up.name, _main._pengawas(s), _main._kunci(db), sh.page)
    try:
        res = fut.result(timeout=120)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Gagal memindai ulang: {e}")
    if not res:
        raise HTTPException(422, "Hasil pindai ulang kosong (halaman tak terbaca)")
    sh.doc_name, sh.scan_status, sh.record, sh.validated = res[0]["doc_name"], res[0]["status"], _main._apply_identity(res[0]["record"], s), False
    db.commit()
    return {"ok": True, "label": classify_scan_status({"status": sh.scan_status}, sh.validated)}


@router.post("/sheets/{shid}/replace")
async def admin_replace_sheet_photo(shid: str, file: FUploadFile = File(...), db=Depends(get_db)):
    """Ganti foto lembar ini dgn berkas baru dari admin. Sama spt endpoint pengawas (POST
    /api/sheets/{shid}/replace) tapi admin-only & TIDAK terhalang sesi sudah disubmit."""
    from server import main as _main
    sh = db.get(Sheet, shid)
    if sh is None:
        raise HTTPException(404, "Lembar tidak ditemukan")
    s = db.get(ScanSession, sh.session_id)
    _main._check_ext(file.filename or "")
    dest = os.path.join(_main._class_folder(s), f"{uuid.uuid4().hex[:8]}_{_main._safe_name(file.filename)}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    size = await _main._save_stream(file, dest)
    fut = _main._submit_scan(dest, file.filename, _main._pengawas(s), _main._kunci(db), 0)
    try:
        res = fut.result(timeout=120)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Gagal memproses foto baru: {e}")
    if not res:
        raise HTTPException(422, "Foto baru tidak berisi halaman")
    up = db.get(UploadFile, sh.file_id)
    up.path, up.name, up.size = dest, file.filename, size
    sh.page, sh.doc_name, sh.scan_status, sh.record, sh.validated = 0, res[0]["doc_name"], res[0]["status"], _main._apply_identity(res[0]["record"], s), False
    db.commit()
    return {"ok": True, "label": classify_scan_status({"status": sh.scan_status}, sh.validated)}


@router.post("/sessions/{sid}/validate")
def admin_validate_session(sid: str, value: bool = True, db=Depends(get_db)):
    """Tandai (atau batalkan tanda) sesi sudah divalidasi admin. Sejak pemindaian dipindah ke latar belakang
    & pengawas cukup unggah foto (validasi per-lembar tak lagi jadi tugas pengawas), aksi ini JUGA
    memfinalisasi sesi -- dulu ini tugas tombol Submit pengawas: memvalidasi semua lembar yg berhasil
    discan (bukan yg gagal), menilai ulang, lalu mengunci sesi (submitted=True) supaya ikut disinkron ke
    Google Sheet & masuk ekspor. Lembar yg gagal (pojok LJK tak terdeteksi) TETAP gagal apa pun statusnya --
    tidak ikut divalidasi, tidak memblokir sisanya (beda dgn submit() lama yg menolak bila ADA yg gagal).
    Hanya bermakna bila pemindaian sudah selesai -- menandai sesi yg masih 'scanning' berisiko
    menyembunyikan lembar yg belum sempat masuk daftar."""
    s = _admin_session_or_404(db, sid)
    if value:
        if _session_status(s) == "scanning":
            raise HTTPException(409, "Pemindaian sesi ini masih berjalan")
        if not s.sheets:
            raise HTTPException(409, "Sesi ini belum punya lembar")
        for sh in s.sheets:
            if classify_scan_status({"status": sh.scan_status}, False) != "Gagal":
                sh.validated = True
        if not s.submitted:
            services.regrade_session(db, s)
            s.submitted, s.submitted_at = True, dt.datetime.now(dt.timezone.utc)
            s.synced_at, s.sync_attempts, s.sync_error, s.sync_next = None, 0, None, None
    s.admin_validated = value
    s.admin_validated_at = dt.datetime.now(dt.timezone.utc) if value else None
    db.commit()
    return {"ok": True, "admin_validated": s.admin_validated, "submitted": s.submitted}


@router.post("/sessions/{sid}/stop")
def admin_stop_session(sid: str, db=Depends(get_db)):
    """Hentikan pemindaian sesi yg masih berjalan (upaya terbaik -- lihat cancel_pending_files di
    server/main.py: berkas yg belum benar2 diambil worker dibatalkan; yg sudah jalan dibiarkan selesai)."""
    from server.main import cancel_pending_files   # impor lokal: hindari impor melingkar (main mengimpor admin)
    s = _admin_session_or_404(db, sid)
    return cancel_pending_files(db, s)


@router.delete("/sessions/{sid}", status_code=204)
def admin_delete_session(sid: str, db=Depends(get_db)):
    """Hapus sesi (kaskade: berkas & lembar ikut terhapus dari DB) + berkas fotonya di disk. Bila sesi masih
    ada pemindaian berjalan, coba hentikan dulu (upaya terbaik) supaya worker tak sia-sia memproses sesi yg
    sebentar lagi lenyap; sisa yg sudah benar2 jalan aman diselesaikan (lihat guard di _on_done). Foto kini
    dibagi per Fakultas/Prodi/Kelas antar sesi sekelas (lihat server/main.py _class_folder) -- HANYA berkas
    milik sesi INI yg dihapus (services.remove_session_files), bukan seluruh folder kelas (bisa ikut
    menghapus foto sesi lain). Daftar berkas diambil SEBELUM db.delete/commit -- setelah itu relasi s.files
    sudah lenyap dari DB."""
    from server.main import cancel_pending_files   # impor lokal: hindari impor melingkar
    s = _admin_session_or_404(db, sid)
    cancel_pending_files(db, s)
    services.remove_session_files(s)
    db.delete(s)
    db.commit()


@router.post("/sessions/{sid}/clear-photos")
def admin_clear_photos(sid: str, db=Depends(get_db)):
    """Hapus foto ASLI yg diunggah (berkas milik sesi ini di disk -- lihat services.remove_session_files)
    utk sesi yg sudah 'validated' (selesai discan + admin sudah menandai beres) -- membebaskan ruang disk.
    TIDAK menyentuh baris DB (ScanSession/UploadFile/Sheet): rekap/nilai/ekspor tetap utuh, cuma berkas
    sumbernya yg lenyap. Setelah ini, pratinjau lembar sesi ini (GET /api/sheets/{shid}/preview) akan
    menjawab 410 (sudah ditangani di sana) -- wajar & disengaja, krn sesi yg sudah divalidasi seharusnya tak
    perlu dipratinjau ulang lagi. Sama seperti pembersihan otomatis berbasis usia (services.cleanup_once),
    hanya dipicu manual & lebih dini (begitu admin menandai validated), bukan menunggu retensi waktu."""
    s = _admin_session_or_404(db, sid)
    if _session_status(s) != "validated":
        raise HTTPException(409, "Sesi baru bisa dibersihkan setelah ditandai validated oleh admin")
    services.remove_session_files(s)
    return {"ok": True, "photos_cleared": True}


# ---------------------------------------------------------------- kunci jawaban
@router.get("/kunci")
def kunci_list(db=Depends(get_db)):
    return [{"name": k.name, "soal": len(k.data), "source": k.source,
             "updated_at": k.updated_at.isoformat() if k.updated_at else None}
            for k in db.scalars(select(Kunci).order_by(Kunci.name))]


@router.put("/kunci/{name}")
def kunci_put(name: str, data: dict, db=Depends(get_db)):
    try:
        services.upsert_kunci(db, name, data, "manual")
    except (ValueError, TypeError):
        raise HTTPException(422, "Format kunci: {\"1\": \"A\", \"2\": \"C\", ...}")
    db.commit()
    return {"ok": True, "soal": len(data)}


@router.delete("/kunci/{name}", status_code=204)
def kunci_delete(name: str, db=Depends(get_db)):
    k = db.get(Kunci, name)
    if k is None:
        raise HTTPException(404, "Kunci tidak ditemukan")
    db.delete(k)
    db.commit()


@router.post("/kunci/upload")
async def kunci_upload(file: FUploadFile = File(...), db=Depends(get_db)):
    """Unggah kunci dari .xlsx (satu tab = satu kode soal) atau .csv (nama berkas = kode soal)."""
    raw = await file.read()
    name = (file.filename or "kunci").rsplit(".", 1)
    ext = name[-1].lower() if len(name) > 1 else ""
    found = {}
    try:
        if ext in ("xlsx", "xlsm", "xls"):
            from core.evaluator import parse_kunci_jawaban_excel
            found = parse_kunci_jawaban_excel(io.BytesIO(raw))
        elif ext == "csv":
            rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
            q = parse_kunci_jawaban_raw_rows(rows)
            if q:
                found[name[0]] = q
        else:
            raise HTTPException(415, "Gunakan berkas .xlsx atau .csv")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Berkas kunci tidak terbaca: {e}")
    found = {k: v for k, v in found.items() if len(v) >= 3}
    if not found:
        raise HTTPException(422, "Tidak ada kunci jawaban valid (minimal 3 soal) di berkas ini")
    for k, v in found.items():
        services.upsert_kunci(db, k, v, "upload")
    db.commit()
    return {"ok": True, "kunci": {k: len(v) for k, v in found.items()}}


@router.post("/kunci/sync")
def kunci_sync():
    return services.sync_kunci_once()


@router.post("/regrade")
def regrade(kelas: str = "", pull: bool = False):
    """Hitung ulang nilai semua sesi yang sudah disubmit dengan kunci terbaru.
    pull=true: tarik kunci dari Google Sheet dulu (bila terkonfigurasi)."""
    pulled = services.sync_kunci_once() if pull else None
    res = services.regrade_all(kelas)
    if pulled is not None:
        res["kunci_ditarik"] = pulled.get("kunci", 0)
        res["kunci_error"] = pulled.get("error")
        res["gsheet_configured"] = pulled.get("configured", False)
    return res


# ---------------------------------------------------------------- sinkron Google Sheet
@router.post("/sync/run")
def sync_run(retry_failed: bool = True, db=Depends(get_db)):
    """Jalankan sinkron sekarang; opsional reset percobaan sesi yang gagal."""
    if retry_failed:
        for s in db.scalars(select(ScanSession).where(ScanSession.submitted.is_(True), ScanSession.synced_at.is_(None))):
            s.sync_attempts, s.sync_next = 0, None
        db.commit()
    return services.sync_pending_once()


@router.post("/cleanup")
def cleanup_run():
    return services.cleanup_once()


# ---------------------------------------------------------------- ekspor
def _export_table(db, kelas: str = "", sid: str = ""):
    cond = [ScanSession.submitted.is_(True)]
    if kelas:
        cond.append(ScanSession.kelas == kelas)
    if sid:
        cond.append(ScanSession.id == sid)
    rows = [sh.record for sh in db.scalars(select(Sheet).join(ScanSession).where(*cond).order_by(ScanSession.submitted_at, Sheet.seq))]
    prefix = ["Submit Date", "Nama Pengawas", "No HP Pengawas", "Ruangan", "Kelas", "File", "NPM", "Nama Mahasiswa",
              "Fakultas", "Program Studi", "Fakultas (LJK)", "Kode Soal", "Jawaban Terisi", "Nilai", "Jumlah Benar", "Jumlah Salah", "Jumlah Kosong", "Kunci Terpakai"]
    seen = {k for r in rows for k in r}
    cols = [c for c in prefix if c in seen]
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return cols, rows


@router.get("/export.csv")
def export_csv(kelas: str = "", sid: str = "", db=Depends(get_db)):
    cols, rows = _export_table(db, kelas, sid)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=rekap_ljk.csv"})


@router.get("/export.xlsx")
def export_xlsx(kelas: str = "", sid: str = "", db=Depends(get_db)):
    from openpyxl import Workbook
    cols, rows = _export_table(db, kelas, sid)
    wb = Workbook()
    ws = wb.active
    ws.title = "Rekap"
    ws.append(cols)
    for r in rows:
        ws.append([r.get(c, "") for c in cols])
    for i, c in enumerate(cols, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(max(len(str(c)) + 2, 10), 32)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=rekap_ljk.xlsx"})
