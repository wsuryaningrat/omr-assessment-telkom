"""Endpoint admin (header X-Admin-Token): ringkasan, sesi, kunci jawaban, sinkron Google Sheet, ekspor,
kalibrasi template."""
import csv
import datetime as dt
import io
import os
import re
import time
import uuid

import cv2
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile as FUploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from core.evaluator import parse_kunci_jawaban_raw_rows
from core.pdf_utils import iter_images_from_file
from scanner.service import CALIB_MAX_OFFSET, apply_field_calib, classify_scan_status, load_default_template
from server import auth, config, plotting, services, sheets
from server.db import AdminAccessLog, AdminUser, Kunci, ScanSession, Sheet, SessionLocal, TemplateCalib, UploadFile


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
    # "lembar" di sini = jumlah FOTO yg berhasil diupload (UploadFile), BUKAN jumlah lembar hasil scan
    # (Sheet) -- supaya progres kelihatan naik begitu pengawas selesai unggah, tanpa perlu nunggu seluruh
    # antrean pemindaian beres (1 foto = 1 UploadFile, tapi bisa jadi >1 Sheet kalau PDF multi-halaman,
    # atau 0 Sheet kalau masih diproses/gagal -- jadi dua angka ini memang bisa beda).
    q = (select(ScanSession.id, ScanSession.kelas, ScanSession.submitted, ScanSession.admin_validated, ScanSession.submitted_at, ScanSession.created_at,
                ScanSession.nama_pengawas, func.count(UploadFile.id))
         .outerjoin(UploadFile, UploadFile.session_id == ScanSession.id).where(*cond).group_by(ScanSession.id))
    by_kelas = {}
    for sid, kelas, sub, adm_val, sub_at, cr_at, nama, n_files in db.execute(q):
        by_kelas.setdefault((kelas or "").strip().lower(), []).append(
            {"id": sid, "kelas": kelas, "submitted": bool(sub), "admin_validated": bool(adm_val), "submitted_at": sub_at, "created_at": cr_at,
             "pengawas": nama, "lembar": int(n_files or 0)})

    days, seen = {}, set()
    for r in slots_src:
        key = r["kelas"].strip().lower(); seen.add(key)
        ss = by_kelas.get(key, [])
        # "selesai" (done) BUKAN cuma krn pengawas sudah submit -- harus sudah DIVALIDASI admin (lihat
        # ScanSession.admin_validated / tombol "Tandai validated" di tab Sesi). Submit-tapi-belum-divalidasi
        # tetap dianggap "berjalan"/Checking, krn masih perlu dicek admin sebelum benar2 dianggap beres.
        done = [x for x in ss if x["admin_validated"]]
        openx = [x for x in ss if not x["admin_validated"]]
        if done:
            status, lembar = "selesai", sum(x["lembar"] for x in done)
            at = max((x["submitted_at"] for x in done if x["submitted_at"]), default=None)
            oleh = done[-1]["pengawas"]
        elif openx:
            status, lembar, at, oleh = "berjalan", sum(x["lembar"] for x in openx), None, openx[-1]["pengawas"]
        else:
            status, lembar, at, oleh = "belum", 0, None, ""
        # Jam upload terakhir (kapan sesi utk kelas ini mulai diunggah) -- dipakai FE sbg ganti kolom nama
        # pengawas di tabel Monitoring (upload_file tak punya kolom waktu per-berkas, jadi dipakai created_at
        # sesi yg paling baru sbg perkiraan terdekat).
        upload_at = max((x["created_at"] for x in ss), default=None)
        d = days.setdefault(r["hari"], {"hari": r["hari"], "slots": []})
        d["slots"].append({**r, "status": status, "lembar": lembar, "submitted_at": at.isoformat() if at else None,
                           "upload_at": upload_at.isoformat() if upload_at else None,
                           "oleh": oleh, "beda_pengawas": bool(oleh) and oleh.strip().lower() != r["pengawas"].strip().lower()})

    out_days = []
    for name in sorted(days, key=lambda h: plotting.HARI.index(h) if h in plotting.HARI else 99):
        d = days[name]; sl = sorted(d["slots"], key=lambda x: (x["jam_mulai"], x["gedung"], x["kelas"]))
        tot = len(sl); selesai = sum(x["status"] == "selesai" for x in sl); jalan = sum(x["status"] == "berjalan" for x in sl)
        mhs = sum(x["jml_mhs"] for x in sl)
        # Upload = TOTAL lembar terunggah lepas dari status validasi (dulu cuma dihitung kalau kelasnya
        # "selesai"/divalidasi, jadi kelas yg masih "Checking" tak ikut kehitung sama sekali walau
        # pengawasnya sudah unggah banyak). Validated = subset yg kelasnya SUDAH divalidasi admin --
        # dua angka ini SENGAJA dipisah supaya progres unggah & progres pengecekan admin kelihatan beda.
        up = sum(min(x["lembar"], x["jml_mhs"]) for x in sl)
        up_validated = sum(min(x["lembar"], x["jml_mhs"]) for x in sl if x["status"] == "selesai")
        out_days.append({"hari": name, "total": tot, "selesai": selesai, "berjalan": jalan, "belum": tot - selesai - jalan,
                         "pct_kelas": round(selesai / tot * 100, 1) if tot else 0, "mhs_total": mhs,
                         "mhs_upload": up, "pct_mhs": round(up / mhs * 100, 1) if mhs else 0,
                         "mhs_validated": up_validated, "pct_validated": round(up_validated / mhs * 100, 1) if mhs else 0,
                         "slots": sl})
    tot = sum(d["total"] for d in out_days); selesai = sum(d["selesai"] for d in out_days)
    mhs = sum(d["mhs_total"] for d in out_days); up = sum(d["mhs_upload"] for d in out_days)
    up_validated = sum(d["mhs_validated"] for d in out_days)
    extra = [{"kelas": x["kelas"], "pengawas": x["pengawas"], "lembar": x["lembar"], "submitted": x["submitted"],
              "submitted_at": x["submitted_at"].isoformat() if x["submitted_at"] else None}
             for k, xs in by_kelas.items() if k not in seen for x in xs][:60]
    wib = dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))
    return {"mode": want or "semua", "hari_ini": plotting.HARI[wib.weekday()], "since": config.MONITOR_SINCE or None,
            "plotting_at": dt.datetime.fromtimestamp(fetched, dt.timezone.utc).isoformat() if fetched else None, "plotting_error": perr,
            "plot_url": config.PLOTTING_SHEET_URL,
            "overall": {"total": tot, "selesai": selesai, "berjalan": sum(d["berjalan"] for d in out_days),
                        "pct_kelas": round(selesai / tot * 100, 1) if tot else 0, "mhs_total": mhs,
                        "mhs_upload": up, "pct_mhs": round(up / mhs * 100, 1) if mhs else 0,
                        "mhs_validated": up_validated, "pct_validated": round(up_validated / mhs * 100, 1) if mhs else 0},
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
    """uploading (berkas sudah diterima server tapi masih menunggu jendela tenang unggah, lihat
    server/main.py _schedule_scan_start -- belum diserahkan ke pool pindai sama sekali) -> scanning (sudah
    diserahkan, masih ada yg diproses) -> perlu_cek (selesai discan, belum tervalidasi admin) -> validated
    (admin sudah menandai selesai dicek). Terpisah dari validasi per-lembar pengawas (Sheet.validated) &
    submit pengawas (ScanSession.submitted) -- lihat _session_row."""
    if any(f.state == "processing" for f in s.files):
        return "scanning"
    if any(f.state == "queued" for f in s.files):
        return "uploading"
    if s.admin_validated:
        return "validated"
    return "perlu_cek"


def _orphan_counts(db, session_ids):
    """Peta session_id -> jumlah berkas 'hilang senyap' (lihat _orphan_files) utk SEKUMPULAN sesi sekaligus,
    lewat SATU query agregat -- bukan satu query per berkas per sesi spt _orphan_files dipanggil langsung
    dlm perulangan. Daftar sesi (GET /api/admin/sessions) memanggil _session_row utk SEMUA baris yg cocok
    filter tiap kali dimuat/dipoling; dgn banyak admin dibuka bersamaan itu jadi query N+1 yg berat & bikin
    tabel Sesi lambat tepat saat banyak admin memvalidasi bersamaan -- lihat _session_row."""
    if not session_ids:
        return {}
    rows = db.execute(
        select(UploadFile.session_id, func.count(UploadFile.id))
        .select_from(UploadFile)
        .outerjoin(Sheet, Sheet.file_id == UploadFile.id)
        .where(UploadFile.session_id.in_(session_ids), UploadFile.state == "done", Sheet.id.is_(None))
        .group_by(UploadFile.session_id)
    )
    return {sid: n for sid, n in rows}


def _session_row(db, s: ScanSession, jml_mhs_map: dict, orphan_count: int | None = None) -> dict:
    from server import main as _main
    n_files = len(s.files)
    n_pending = sum(1 for f in s.files if f.state in ("queued", "processing"))
    n_failed = sum(1 for f in s.files if f.state == "failed")
    n_orphans = len(_orphan_files(db, s)) if orphan_count is None else orphan_count
    n_rescanning = _main._RESCAN_PENDING.get(s.id, 0)
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
        "rescanning": n_rescanning,
        "status": _session_status(s),
        "admin_validated": s.admin_validated,
        "admin_validated_at": s.admin_validated_at.isoformat() if s.admin_validated_at else None,
        "admin_validated_by": s.admin_validated_by,
        "photos_cleared": photos_cleared,
    }


_DERIVED_STATUSES = ("uploading", "scanning", "perlu_cek", "validated")
_SORT_KEYS = ("created_at", "nama", "kelas", "lembar", "jml_mhs", "status", "hari_ujian")
_STATUS_ORDER = {"uploading": 0, "scanning": 1, "perlu_cek": 2, "validated": 3}


def _sort_rows(rows, key, asc):
    """Urutkan baris sesi menurut kolom yg diklik di tabel Sesi. Nilai kosong (None/"") SELALU di akhir,
    apa pun arahnya, supaya tak menutupi data nyata di puncak daftar."""
    def val(r):
        if key == "lembar":
            return r["files"]["total"]
        if key == "status":
            return _STATUS_ORDER.get(r["status"], 9)
        v = r.get(key)
        return v.lower() if isinstance(v, str) else v
    filled = [r for r in rows if val(r) not in (None, "")]
    empty = [r for r in rows if val(r) in (None, "")]
    return sorted(filled, key=val, reverse=not asc) + empty




@router.get("/sessions")
def sessions(page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100), q: str = "", status: str = "all", hari: str = "", sort: str = "", dir: str = "desc", db=Depends(get_db)):
    cond = []
    if status == "submitted":
        cond.append(ScanSession.submitted.is_(True))
    elif status == "open":
        cond.append(ScanSession.submitted.is_(False))
    elif status == "unsynced":
        cond += [ScanSession.submitted.is_(True), ScanSession.synced_at.is_(None)]
    if hari.strip():
        cond.append(ScanSession.hari_ujian == hari.strip())
    if q.strip():
        like = f"%{q.strip()}%"
        cond.append(ScanSession.nama_pengawas.ilike(like) | ScanSession.kelas.ilike(like) | ScanSession.prodi.ilike(like))
    jml_mhs_map = _jml_mhs_by_kelas()
    if status in _DERIVED_STATUSES or sort in _SORT_KEYS:
        # scanning/perlu_cek/validated butuh f.state per berkas & s.admin_validated, dan kolom turunan
        # (lembar/mhs/status) tak ada di SQL -- jadi disaring, diurutkan, & dipaginasi di Python. Jumlah sesi
        # total (bukan per kelas ujian) di alat ini kecil, jadi memuat semua baris yg cocok `q` sekali aman.
        # selectinload(files/sheets) + _orphan_counts BATCH: tanpa ini, tiap baris memicu query LAZY-LOAD
        # terpisah utk files & sheets, PLUS satu query per berkas utk cek orphan (lihat _orphan_counts) --
        # N+1 yg baru kerasa lambat saat daftar sesi dimuat/dipoling banyak admin bersamaan.
        all_rows = db.scalars(select(ScanSession).where(*cond)
                               .options(selectinload(ScanSession.files), selectinload(ScanSession.sheets))
                               .order_by(ScanSession.created_at.desc())).all()
        orphans = _orphan_counts(db, [s.id for s in all_rows])
        items_all = [_session_row(db, s, jml_mhs_map, orphans.get(s.id, 0)) for s in all_rows
                     if status not in _DERIVED_STATUSES or _session_status(s) == status]
        if sort in _SORT_KEYS:
            items_all = _sort_rows(items_all, sort, dir == "asc")
        total = len(items_all)
        items = items_all[(page - 1) * size: (page - 1) * size + size]
    else:
        total = db.scalar(select(func.count()).select_from(ScanSession).where(*cond)) or 0
        rows = db.scalars(select(ScanSession).where(*cond)
                           .options(selectinload(ScanSession.files), selectinload(ScanSession.sheets))
                           .order_by(ScanSession.created_at.desc()).offset((page - 1) * size).limit(size)).all()
        orphans = _orphan_counts(db, [s.id for s in rows])
        items = [_session_row(db, s, jml_mhs_map, orphans.get(s.id, 0)) for s in rows]
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
    from server import main as _main
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
            "scan_cached": os.path.exists(_main._scan_cache_path(sh.id)),
            "record": r,   # dipakai form edit di menu Kalibrasi/Detail (NPM/kode soal/fakultas/tiap jawaban) -- lihat admin_edit_sheet
        })
    orphans = [{"id": f.id, "name": f.name, "size": f.size} for f in _orphan_files(db, s)]
    return {"items": items, "orphan_files": orphans}


@router.post("/sessions/{sid}/sheets/add")
async def admin_add_sheet_photo(sid: str, file: FUploadFile = File(...), db=Depends(get_db)):
    """Tambah lembar BARU ke sesi ini dari foto yg diunggah admin -- dipakai saat pengawas lupa mengunggah
    satu lembar mahasiswa (bukan mengganti foto lembar yg sudah ada, lihat admin_replace_sheet_photo).
    Dipindai LANGSUNG (sinkron, blocking spt endpoint ganti-foto admin lain), bukan lewat antrean/jendela
    tenang biasa (lihat server/main.py upload_files), supaya admin langsung tahu hasilnya di popup Detail.
    Jumlah "lembar" sesi (tabel Sesi, ekspor, dsb) otomatis ikut bertambah krn hanya menghitung baris Sheet,
    apa pun asal/jalur pembuatannya -- tak ada penghitungan terpisah yg perlu disentuh. Mendukung PDF
    multi-halaman jg (tiap halaman -> satu Sheet), spt jalur unggah pengawas biasa."""
    from server import main as _main
    s = _admin_session_or_404(db, sid)
    _main._check_ext(file.filename or "")
    folder = _main._class_folder(s)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"{uuid.uuid4().hex[:8]}_{_main._safe_name(file.filename)}")
    size = await _main._save_stream(file, dest)
    rec = UploadFile(session_id=s.id, name=file.filename or "berkas", size=size, path=dest, state="processing")
    db.add(rec)
    db.commit()
    fut = _main._submit_scan(dest, file.filename, _main._pengawas(s), _main._kunci(db), with_overlay=True, calib=_main._calib(db))
    try:
        res = fut.result(timeout=120)
    except Exception as e:  # noqa: BLE001
        rec.state, rec.error = "failed", f"{type(e).__name__}: {e}"
        db.commit()
        raise HTTPException(422, f"Gagal memindai foto baru: {e}")
    if not res:
        rec.state, rec.error = "failed", "Halaman tak terbaca"
        db.commit()
        raise HTTPException(422, "Foto tidak berisi halaman yang terbaca")
    seq_base = db.scalar(select(func.count()).select_from(Sheet).where(Sheet.session_id == s.id)) or 0
    created = []
    for i, r in enumerate(res):
        sh = Sheet(session_id=s.id, file_id=rec.id, page=r["page"], seq=seq_base + i + 1,
                   doc_name=r["doc_name"], scan_status=r["status"], record=_main._apply_identity(r["record"], s))
        db.add(sh)
        db.flush()
        _main._store_overlay(sh.id, r)
        created.append(sh)
    rec.state = "done"
    db.commit()
    return {"ok": True, "lembar": len(created), "sheet_ids": [x.id for x in created],
            "label": classify_scan_status({"status": created[0].scan_status}, False)}


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


@router.post("/sessions/{sid}/rescan-all")
def admin_rescan_all(sid: str, db=Depends(get_db)):
    """Pindai ulang SEMUA lembar sesi ini (bukan cuma yg 'hilang senyap' -- lihat reprocess-orphans di atas
    utk itu), dgn foto sumber yg SAMA. Dipakai admin mis. setelah perbaikan deteksi pojok/kontras supaya
    lembar yg sudah lama discan ikut disegarkan tanpa pengawas foto ulang. Berjalan di LATAR BELAKANG (lihat
    _main._enqueue_rescan) krn bisa lama utk sesi berisi banyak lembar -- progresnya keluar lewat field
    `rescanning` pada GET /sessions selagi berjalan. Lembar yg foto sumbernya sudah tak ada (mis. sudah
    'Bersihkan foto') dilewati begitu saja, bukan dianggap gagal."""
    from server import main as _main
    s = _admin_session_or_404(db, sid)
    all_sheets = list(s.sheets)
    queued = sum(1 for sh in all_sheets if _main._enqueue_rescan(sh.id))
    return {"ok": True, "diantre": queued, "dilewati": len(all_sheets) - queued}


class _RescanSelectedIn(BaseModel):
    sheet_ids: list[str]


@router.post("/sessions/{sid}/rescan-selected")
def admin_rescan_selected(sid: str, body: _RescanSelectedIn, db=Depends(get_db)):
    """Sama spt rescan-all, tapi cuma utk lembar yg dicentang admin di popup Detail lembar (checkbox per
    baris) -- berguna kalau cuma sebagian lembar yg perlu disegarkan (mis. yg kelihatan salah baca),
    tanpa perlu menunggu SEMUA lembar sesi ikut dipindai ulang."""
    from server import main as _main
    s = _admin_session_or_404(db, sid)
    ids = set(body.sheet_ids)
    sheets_to_rescan = [sh for sh in s.sheets if sh.id in ids]
    queued = sum(1 for sh in sheets_to_rescan if _main._enqueue_rescan(sh.id))
    return {"ok": True, "diantre": queued, "dilewati": len(sheets_to_rescan) - queued}


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


class _AdminSheetEditIn(BaseModel):
    npm: str | None = None
    kode_soal: str | None = None
    fakultas_ljk: str | None = None
    jawaban: dict[str, str] | None = None   # {"1": "A", "02": "BLANK", ...} -- nomor soal boleh tanpa nol di depan
    kuisioner: dict[str, str] | None = None   # {"1": "A", ...} -- kunci "qNN" di record, format sama spt jawaban


def _apply_sheet_edits(rec: dict, body: "_AdminSheetEditIn") -> dict:
    """Terapkan koreksi manual (dipakai admin_edit_sheet & admin_bulk_edit) ke SALINAN record `rec`.
    Sama seperti koreksi pengawas (main.correct_sheet: NPM/kode soal/fakultas) DITAMBAH koreksi per
    jawaban -- pengawas tak diberi ini krn nama mahasiswa & jawaban org lain sengaja disembunyikan dari
    dashboard pengawas (lihat main._sheet_view)."""
    from server import main as _main
    rec = dict(rec)
    if body.npm is not None:
        npm = re.sub(r"\D", "", body.npm)
        was = re.sub(r"\D", "", str(rec.get("NPM", "")))
        if npm != was and npm and len(npm) != 10:   # hasil OCR lama boleh sudah tak 10 digit -- itu bukan
            raise HTTPException(422, "NPM harus 10 digit")   # salah admin; tolak hanya kalau diganti ke nilai baru yg tak valid
        rec["NPM"] = npm
    if body.kode_soal is not None:
        rec["Kode Soal"] = body.kode_soal.strip()
    if body.fakultas_ljk is not None:
        fak = body.fakultas_ljk.strip().upper()
        was = str(rec.get("Fakultas (LJK)", "")).strip().upper()
        if fak != was:   # hasil OCR lama boleh sudah tak valid (mis. "MULTIPLE") -- itu bukan salah admin,
            # jadi jangan tolak kalau nilainya SAMA & tak diubah; tolak hanya kalau memang diganti ke nilai baru yg tak valid.
            options = _main._fakultas_options()
            if fak and fak not in options:
                raise HTTPException(422, f"Fakultas harus salah satu dari: {', '.join(options)}")
        rec["Fakultas (LJK)"] = fak
        rec["Fakultas"] = fak
    if body.jawaban:
        for q, ans in body.jawaban.items():
            m = re.fullmatch(r"0*(\d{1,2})", q.strip())
            if not m:
                continue
            val = (ans or "").strip().upper()
            rec[f"soal_{int(m.group(1)):02d}"] = val if val and val != "-" else "BLANK"
    if body.kuisioner:
        for q, ans in body.kuisioner.items():
            m = re.fullmatch(r"0*(\d{1,2})", q.strip())
            if not m:
                continue
            val = (ans or "").strip().upper()
            rec[f"q{int(m.group(1)):02d}"] = val if val and val != "-" else "BLANK"
    return rec


def _regrade_record(db, rec: dict) -> dict:
    from core.evaluator import grade_student_record
    k = services.kunci_int(db)
    return grade_student_record(rec, k) if k else rec


@router.patch("/sheets/{shid}")
def admin_edit_sheet(shid: str, body: _AdminSheetEditIn, db=Depends(get_db)):
    """Koreksi manual NPM/kode soal/fakultas/JAWABAN per lembar dari admin -- spt endpoint pengawas (PATCH
    /api/sheets/{shid}) tapi admin-only, boleh koreksi jawaban juga, & TIDAK terhalang sesi sudah disubmit
    (sesi yg sudah divalidasi/disubmit tetap bisa dibetulkan kalau ternyata masih ada salah baca). Dipakai
    panel edit 3-kolom di Detail sesi. Menilai ulang otomatis & MEMBATALKAN validasi lembar ini (data
    berubah -> perlu dicek ulang, jangan biarkan status lama menyesatkan)."""
    from server import main as _main
    sh = db.get(Sheet, shid)
    if sh is None:
        raise HTTPException(404, "Lembar tidak ditemukan")
    rec = _apply_sheet_edits(sh.record, body)
    sh.record = _regrade_record(db, rec)
    sh.validated = False
    db.commit()
    up = db.get(UploadFile, sh.file_id)
    return {"id": sh.id, "seq": sh.seq, "file": rec.get("File"), "nama": rec.get("Nama Mahasiswa"),
            "npm": rec.get("NPM"), "kode_soal": rec.get("Kode Soal"), "fakultas_ljk": rec.get("Fakultas (LJK)"),
            "terisi": sh.record.get("Jawaban Terisi"), "nilai": sh.record.get("Nilai"),
            "label": classify_scan_status({"status": sh.scan_status}, sh.validated), "validated": sh.validated,
            "photo_exists": bool(up and up.path and os.path.exists(up.path)),
            "scan_cached": os.path.exists(_main._scan_cache_path(sh.id)), "record": sh.record}


class _BulkEditIn(BaseModel):
    kode_soal: str | None = None
    fakultas_ljk: str | None = None


@router.post("/sessions/{sid}/bulk-edit")
def admin_bulk_edit(sid: str, body: _BulkEditIn, db=Depends(get_db)):
    """Terapkan koreksi Kode Soal dan/atau Fakultas ke SEMUA lembar sesi ini sekaligus -- dipakai saat
    satu blok LJK terbaca konsisten salah utk seluruh kelas (mis. kolom kode soal ketutup gambar tercetak
    miring, atau fakultas ketukar semua krn kelas gabungan). Tiap lembar dinilai ulang; TIDAK menyentuh
    NPM/jawaban (beda per mahasiswa, tak masuk akal diseragamkan)."""
    s = _admin_session_or_404(db, sid)
    if body.kode_soal is None and body.fakultas_ljk is None:
        raise HTTPException(422, "Isi kode_soal dan/atau fakultas_ljk")
    edit = _AdminSheetEditIn(kode_soal=body.kode_soal, fakultas_ljk=body.fakultas_ljk)
    n = 0
    for sh in s.sheets:
        rec = _apply_sheet_edits(sh.record, edit)
        sh.record = _regrade_record(db, rec)
        sh.validated = False
        n += 1
    db.commit()
    return {"ok": True, "diubah": n}


@router.post("/sessions/{sid}/validate-all-sheets")
def admin_validate_all_sheets(sid: str, db=Depends(get_db)):
    """Validasi SEMUA lembar sesi ini sekaligus (Sheet.validated=True, KECUALI yg "Gagal" terbaca -- sama
    spt admin_validate_sheet per-lembar, lembar gagal tak bisa divalidasi) -- lawan dari
    admin_unvalidate_all_sheets. Admin-only & TIDAK terhalang sesi sudah disubmit (beda dgn endpoint
    pengawas POST /api/sessions/{sid}/validate-all yg menolak bila sesi sudah disubmit). Dipakai tombol
    "Validasi semua" di popup Detail lembar supaya admin tak perlu mencentang satu2 kalau semua lembar
    memang sudah benar."""
    s = _admin_session_or_404(db, sid)
    n = 0
    for sh in s.sheets:
        if not sh.validated and classify_scan_status({"status": sh.scan_status}, False) != "Gagal":
            sh.validated = True
            n += 1
    db.commit()
    return {"ok": True, "divalidasi": n}


@router.post("/sessions/{sid}/unvalidate-all-sheets")
def admin_unvalidate_all_sheets(sid: str, db=Depends(get_db)):
    """Batalkan validasi SEMUA lembar sesi ini sekaligus (Sheet.validated=False utk tiap lembar) --
    BEDA dgn POST .../validate?value=false (admin_validate_session), yg cuma membatalkan tanda
    admin_validated di level SESI & TAK menyentuh validated per lembar (lembar yg sudah ditandai valid
    tetap valid di situ). Dipakai saat admin mau mengecek ulang semua lembar dari nol, mis. stlh
    "Pindai ulang semua foto" supaya status lama tak menyesatkan."""
    s = _admin_session_or_404(db, sid)
    n = sum(1 for sh in s.sheets if sh.validated)
    for sh in s.sheets:
        sh.validated = False
    db.commit()
    return {"ok": True, "dibatalkan": n}


@router.post("/sheets/{shid}/validate")
def admin_validate_sheet(shid: str, value: bool = True, db=Depends(get_db)):
    """Tandai (atau batalkan tanda) SATU lembar sudah divalidasi -- spt endpoint pengawas (POST
    /api/sheets/{shid}/validate|/unvalidate) tapi admin-only & TIDAK terhalang sesi sudah disubmit.
    Dipakai tombol centang di daftar lembar (kolom kiri Detail sesi) utk validasi cepat per-lembar
    tanpa perlu memvalidasi seisi sesi lewat "Tandai validated"."""
    sh = db.get(Sheet, shid)
    if sh is None:
        raise HTTPException(404, "Lembar tidak ditemukan")
    if value and classify_scan_status({"status": sh.scan_status}, False) == "Gagal":
        raise HTTPException(409, "Lembar gagal terbaca -- tak bisa divalidasi, ganti foto atau pindai ulang dulu")
    sh.validated = value
    db.commit()
    return {"ok": True, "validated": sh.validated, "label": classify_scan_status({"status": sh.scan_status}, sh.validated)}


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
    fut = _main._submit_scan(up.path, up.name, _main._pengawas(s), _main._kunci(db), sh.page, True, calib=_main._calib(db))
    try:
        res = fut.result(timeout=120)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Gagal memindai ulang: {e}")
    if not res:
        raise HTTPException(422, "Hasil pindai ulang kosong (halaman tak terbaca)")
    sh.doc_name, sh.scan_status, sh.record, sh.validated = res[0]["doc_name"], res[0]["status"], _main._apply_identity(res[0]["record"], s), False
    db.commit()
    _main._store_overlay(shid, res[0])
    return {"ok": True, "label": classify_scan_status({"status": sh.scan_status}, sh.validated)}


@router.post("/sheets/{shid}/rotate")
def admin_rotate_sheet_photo(shid: str, deg: int = Query(..., description="90 (searah jarum jam) atau -90 (berlawanan)"), db=Depends(get_db)):
    """Putar foto ASLI lembar ini 90 derajat lalu pindai ulang -- dipakai saat pengawas memfoto LJK
    miring/terbalik sehingga marker tak terdeteksi (klasifikasi "Gagal"). Berkas DITIMPA DI TEMPAT (path
    tak berubah, admin_rescan_sheet jg memakai path yg sama) -- bukan ganti foto baru spt admin_replace_sheet_photo.
    Hanya utk foto (JPG/PNG/HEIC/WEBP) -- PDF tak punya "satu foto asli" yg bisa diputar di tempat (pemindai
    me-render tiap halamannya langsung dari berkas PDF saat memindai, lihat core/pdf_utils.iter_images_from_file)."""
    from server import main as _main
    from PIL import Image, ImageOps
    if deg not in (90, -90):
        raise HTTPException(422, "deg harus 90 atau -90")
    sh = db.get(Sheet, shid)
    if sh is None:
        raise HTTPException(404, "Lembar tidak ditemukan")
    s = db.get(ScanSession, sh.session_id)
    up = db.get(UploadFile, sh.file_id)
    if not up or not up.path or not os.path.exists(up.path):
        raise HTTPException(410, "Berkas sumber sudah tak ada (mis. sudah 'Bersihkan foto')")
    ext = up.name.rsplit(".", 1)[-1].lower() if "." in up.name else ""
    if ext == "pdf":
        raise HTTPException(415, "Rotasi hanya didukung untuk foto (JPG/PNG/HEIC/WEBP), bukan PDF")
    try:
        img = Image.open(up.path)
        img.load()
        img = ImageOps.exif_transpose(img).convert("RGB")   # samakan dgn orientasi yg dipakai pemindai
        img = img.rotate(-deg, expand=True)                 # PIL rotate(): sudut positif = berlawanan jarum jam
        # HEIC/HEIF: PIL (bahkan dgn pillow-heif) umumnya tak bisa MENULIS format ini -- simpan sbg JPEG &
        # perbarui UploadFile.name (bukan .path) spy pemindaian selanjutnya membaca ekstensi yg BENAR
        # (worker memilih dekoder dari nama berkas, lihat core/pdf_utils.load_image_with_exif).
        if ext in ("heic", "heif"):
            img.save(up.path, format="JPEG", quality=92)
            up.name = re.sub(r"\.\w+$", ".jpg", up.name)
        elif ext in ("jpg", "jpeg"):
            img.save(up.path, format="JPEG", quality=92)
        else:
            img.save(up.path, format=ext.upper())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Gagal memutar foto: {e}")
    up.size = os.path.getsize(up.path)
    db.commit()
    fut = _main._submit_scan(up.path, up.name, _main._pengawas(s), _main._kunci(db), sh.page, True, calib=_main._calib(db))
    try:
        res = fut.result(timeout=120)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Foto sudah diputar, tapi gagal memindai ulang: {e}")
    if not res:
        raise HTTPException(422, "Foto sudah diputar, tapi hasil pindai ulang kosong (halaman tak terbaca)")
    sh.doc_name, sh.scan_status, sh.record, sh.validated = res[0]["doc_name"], res[0]["status"], _main._apply_identity(res[0]["record"], s), False
    db.commit()
    _main._clear_scan_cache(shid)
    _main._store_overlay(shid, res[0])
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
    fut = _main._submit_scan(dest, file.filename, _main._pengawas(s), _main._kunci(db), 0, True, calib=_main._calib(db))
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
    _main._store_overlay(shid, res[0])
    return {"ok": True, "label": classify_scan_status({"status": sh.scan_status}, sh.validated)}


@router.delete("/sheets/{shid}", status_code=204)
def admin_delete_sheet(shid: str, db=Depends(get_db)):
    """Hapus SATU lembar (mis. salah foto/bukan LJK/duplikat) -- admin-only & TIDAK terhalang sesi sudah
    disubmit (beda dgn endpoint pengawas DELETE /api/sheets/{shid}, yg menolak bila sesi sudah disubmit).
    Foto sumbernya ikut terhapus dari disk & tak tersisa jadi "orphan" -- lihat main._delete_sheet_and_cleanup."""
    from server import main as _main
    sh = db.get(Sheet, shid)
    if sh is None:
        raise HTTPException(404, "Lembar tidak ditemukan")
    _main._delete_sheet_and_cleanup(db, sh)
    db.commit()


class _DeleteSelectedIn(BaseModel):
    sheet_ids: list[str]


@router.post("/sessions/{sid}/delete-selected")
def admin_delete_selected_sheets(sid: str, body: _DeleteSelectedIn, db=Depends(get_db)):
    """Hapus SEKALIGUS lembar2 yg dicentang admin di popup Detail lembar (checkbox per baris, sama dgn yg
    dipakai "Pindai ulang terpilih" -- lihat admin_rescan_selected) -- mis. beberapa lembar salah
    foto/duplikat/bukan LJK ketemu sekaligus. Sama spt DELETE /sheets/{shid} (admin-only, tak terhalang
    sesi tersubmit, foto ikut terhapus) tapi utk banyak lembar dlm SATU permintaan."""
    from server import main as _main
    s = _admin_session_or_404(db, sid)
    ids = set(body.sheet_ids)
    to_delete = [sh for sh in s.sheets if sh.id in ids]
    for sh in to_delete:
        _main._delete_sheet_and_cleanup(db, sh)
    db.commit()
    return {"ok": True, "dihapus": len(to_delete)}


@router.post("/sessions/{sid}/validate")
def admin_validate_session(sid: str, value: bool = True, db=Depends(get_db), admin=Depends(auth.require_admin)):
    """Tandai (atau batalkan tanda) sesi sudah divalidasi admin -- QA murni: memvalidasi semua lembar yg
    berhasil discan (bukan yg gagal) & menilai ulang dgn kunci saat ini. "validated" & "sent" SENGAJA
    dipisah (2 Okt 2026): aksi ini TIDAK LAGI ikut mengunci/mengirim sesi (submitted) -- itu baru terjadi
    kalau admin SENGAJA menekan "Kirim" (lihat admin_sync_session_now), supaya tak ada sesi yg nyasar
    terkirim ke Google Sheet produksi sebelum admin benar2 menekan tombol itu. Lembar yg gagal (pojok LJK
    tak terdeteksi) TETAP gagal apa pun statusnya -- tidak ikut divalidasi, tidak memblokir sisanya (beda
    dgn submit() lama yg menolak bila ADA yg gagal). Hanya bermakna bila pemindaian sudah selesai --
    menandai sesi yg masih 'scanning' berisiko menyembunyikan lembar yg belum sempat masuk daftar.
    Identitas admin yg menandai dicatat di admin_validated_by (nama/username/email -- lihat
    auth.require_admin) supaya tampil di detail sesi."""
    s = _admin_session_or_404(db, sid)
    if value:
        if _session_status(s) in ("scanning", "uploading"):
            raise HTTPException(409, "Pemindaian sesi ini masih berjalan")
        if not s.sheets:
            raise HTTPException(409, "Sesi ini belum punya lembar")
        for sh in s.sheets:
            if classify_scan_status({"status": sh.scan_status}, False) != "Gagal":
                sh.validated = True
        services.regrade_session(db, s)
    s.admin_validated = value
    s.admin_validated_at = dt.datetime.now(dt.timezone.utc) if value else None
    s.admin_validated_by = (admin.get("name") or admin.get("email") or "?") if value else None
    db.commit()
    return {"ok": True, "admin_validated": s.admin_validated, "admin_validated_by": s.admin_validated_by, "submitted": s.submitted}


@router.post("/sessions/{sid}/sync-now")
def admin_sync_session_now(sid: str, db=Depends(get_db)):
    """"Kirim": SATU-SATUNYA jalan sesi terkirim ke Google Sheet sejak "validated" & "sent" dipisah (lihat
    admin_validate_session) -- tombol manual (ikon kirim/upload) di tabel Sesi & popup Detail lembar.
    HANYA bisa dipanggil setelah admin_validated=True (admin harus SENGAJA menandai validated dulu),
    supaya tak ada sesi yg terkirim tanpa admin benar2 menekan tombol ini. Mengunci sesi dulu bila belum
    (submitted=True, nilai ulang dgn kunci saat ini) lalu langsung sinkron -- aman dipanggil berulang
    (upsert per NPM, lihat services.sync_session_now), jg dipakai utk kirim ULANG sesi yg datanya berubah
    stlh koreksi via Detail lembar, atau mencoba lagi setelah gagal (kuota/jaringan)."""
    s = _admin_session_or_404(db, sid)
    if not s.admin_validated:
        raise HTTPException(409, "Sesi belum ditandai validated -- validasi dulu sebelum mengirim")
    if not s.submitted:
        services.regrade_session(db, s)
        s.submitted, s.submitted_at = True, dt.datetime.now(dt.timezone.utc)
        db.commit()
    res = services.sync_session_now(db, s)
    if not res["ok"]:
        raise HTTPException(409, res["error"])
    return res


@router.post("/sessions/{sid}/stop")
def admin_stop_session(sid: str, db=Depends(get_db)):
    """Hentikan pemindaian sesi yg masih berjalan (upaya terbaik -- lihat cancel_pending_files di
    server/main.py: berkas yg belum benar2 diambil worker dibatalkan; yg sudah jalan dibiarkan selesai)."""
    from server.main import cancel_pending_files   # impor lokal: hindari impor melingkar (main mengimpor admin)
    s = _admin_session_or_404(db, sid)
    return cancel_pending_files(db, s)


@router.delete("/sessions/{sid}", status_code=204, dependencies=[Depends(auth.require_superuser)])
def admin_delete_session(sid: str, db=Depends(get_db)):
    """Khusus super_admin (auth.require_superuser). Hapus sesi (kaskade: berkas & lembar ikut terhapus dari DB) + berkas fotonya di disk. Bila sesi masih
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


@router.post("/migrate-storage")
def admin_migrate_storage(db=Depends(get_db)):
    """Pindahkan berkas sesi LAMA (sebelum foto ditata per Fakultas/Prodi/Kelas) ke struktur folder baru --
    aman dipanggil berkali-kali, cuma memindah yg belum sesuai (lihat services.migrate_old_storage)."""
    return services.migrate_old_storage(db)


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


# ---------------------------------------------------------------- kalibrasi template
def _calib_template():
    tpl = load_default_template()
    if not tpl:
        raise HTTPException(500, "Template pemindai tidak ditemukan")
    return tpl


class _RawUpload:
    """Adaptor kecil spy bytes yg sudah dibaca di memori (bukan berkas di disk) bisa dipakai langsung dgn
    core.pdf_utils.iter_images_from_file (butuh .name + .getvalue())."""
    def __init__(self, name, data):
        self.name = name
        self._data = data

    def getvalue(self):
        return self._data


def _calib_tmp_dir():
    d = os.path.join(config.UPLOAD_DIR, "_calib_tmp")
    os.makedirs(d, exist_ok=True)
    return d


def _sweep_calib_tmp(max_age_s=7200):
    """Foto referensi kalibrasi cuma dipakai sementara selagi admin menyesuaikan offset di layar --
    sapu yg lebih tua dari 2 jam tiap ada unggahan baru, drpd menambah tugas latar belakang terpisah
    cuma utk ini (bandingkan services.cleanup_once, yg utk foto sesi ujian sungguhan)."""
    d = _calib_tmp_dir()
    now = time.time()
    for fn in os.listdir(d):
        p = os.path.join(d, fn)
        try:
            if now - os.path.getmtime(p) > max_age_s:
                os.remove(p)
        except OSError:
            pass


_CALIB_SEL_COLOR = (40, 180, 60)     # hijau (BGR) -- blok yg sedang dikalibrasi: garis besar + tiap bubble
_CALIB_DIM_COLOR = (150, 150, 150)   # abu -- blok lain, cuma garis besar biar admin tetap dpt konteks posisi


def _draw_calib_overlay(warped_bgr, fields_dict, selected=None):
    img = warped_bgr.copy()
    for name, fdef in fields_dict.items():
        roi = fdef.get("roi")
        if not roi:
            continue
        is_sel = name == selected
        color = _CALIB_SEL_COLOR if is_sel else _CALIB_DIM_COLOR
        x, y, w, h = (int(round(v)) for v in roi)
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 3 if is_sel else 1)
        cv2.putText(img, name, (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color,
                    2 if is_sel else 1, cv2.LINE_AA)
        if is_sel:   # blok yg dikalibrasi: tampilkan tiap kotak bubble, bukan cuma garis besar ROI
            for it in fdef.get("items", []):
                for b in it.get("bubbles", []):
                    bw, bh = b.get("w", 24), b.get("h", 24)
                    x0, y0 = int(round(b["cx"] - bw / 2)), int(round(b["cy"] - bh / 2))
                    x1, y1 = int(round(b["cx"] + bw / 2)), int(round(b["cy"] + bh / 2))
                    cv2.rectangle(img, (x0, y0), (x1, y1), (0, 140, 255), 1)
    return img


@router.get("/calib/fields")
def calib_fields(db=Depends(get_db)):
    """Daftar blok template (NAMA, NPM, KODE SOAL, Soal-A, dst) + koreksi (dx, dy) yg tersimpan utk tiap
    blok -- dipakai mengisi dropdown & tabel ringkasan di menu Kalibrasi admin."""
    tpl = _calib_template()
    saved = {c.field_name: c for c in db.scalars(select(TemplateCalib))}
    fields = []
    for name, fdef in tpl.get("fields", {}).items():
        c = saved.get(name)
        n_bubbles = sum(len(it.get("bubbles", [])) for it in fdef.get("items", []))
        fields.append({"name": name, "n_bubbles": n_bubbles, "dx": c.dx if c else 0.0, "dy": c.dy if c else 0.0,
                        "updated_at": c.updated_at.isoformat() if c else None})
    return {"canvas": tpl.get("canvas", {"width": 1700, "height": 2400}), "fields": fields, "max_offset": CALIB_MAX_OFFSET}


class _CalibIn(BaseModel):
    dx: float = 0.0
    dy: float = 0.0


@router.put("/calib/fields/{field_name}")
def calib_set(field_name: str, body: _CalibIn, db=Depends(get_db)):
    """Simpan koreksi (dx, dy) blok ini -- dipakai SEJAK SEKARANG oleh pemindaian baru (lihat
    server.main._calib & scanner.service.apply_field_calib). Lembar yg SUDAH discan sebelumnya TIDAK ikut
    berubah otomatis -- pakai "Pindai ulang semua lembar" di tab Sesi kalau perlu disegarkan."""
    tpl = _calib_template()
    if field_name not in tpl.get("fields", {}):
        raise HTTPException(404, "Blok tidak dikenal di template")
    dx = max(-CALIB_MAX_OFFSET, min(CALIB_MAX_OFFSET, body.dx))
    dy = max(-CALIB_MAX_OFFSET, min(CALIB_MAX_OFFSET, body.dy))
    c = db.get(TemplateCalib, field_name)
    if c is None:
        c = TemplateCalib(field_name=field_name)
        db.add(c)
    c.dx, c.dy = dx, dy
    db.commit()
    return {"ok": True, "field": field_name, "dx": dx, "dy": dy}


@router.delete("/calib/fields/{field_name}")
def calib_clear(field_name: str, db=Depends(get_db)):
    """Kembalikan blok ini ke posisi asli template (hapus koreksi tersimpan)."""
    c = db.get(TemplateCalib, field_name)
    if c is not None:
        db.delete(c)
        db.commit()
    return {"ok": True}


@router.post("/calib/reset")
def calib_reset_all(db=Depends(get_db)):
    """Hapus SEMUA koreksi kalibrasi tersimpan -- kembali ke posisi asli template utk semua blok."""
    n = db.scalar(select(func.count()).select_from(TemplateCalib)) or 0
    db.query(TemplateCalib).delete()
    db.commit()
    return {"ok": True, "direset": n}


@router.post("/calib/upload")
async def calib_upload(file: FUploadFile = File(...), page: int = Query(0, ge=0)):
    """Simpan foto referensi kalibrasi sementara (2 jam, lihat _sweep_calib_tmp) & jalankan deteksi
    pojok+crop SEKALI -- tahap termahal. Pratinjau berikutnya (GET /calib/preview, dipanggil tiap admin
    geser offset) tinggal gambar ulang kotak di atas hasil crop yg sudah dicache ini, jadi cepat & foto
    tak perlu dikirim ulang tiap geser. Cocok dipakai dgn foto LJK APA SAJA (kosong ataupun sudah
    diarsir) -- cuma posisi kotaknya yg dicek di sini, bukan isinya."""
    from core.alignment import detect_corners_and_crop
    _sweep_calib_tmp()
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "Berkas kosong")
    try:
        images = list(iter_images_from_file(_RawUpload(file.filename or "foto.jpg", raw), max_side=2200))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Gagal membaca berkas: {e}")
    if page >= len(images):
        raise HTTPException(404, "Halaman tak ditemukan dlm berkas")
    _name, img_bgr = images[page]
    tpl = _calib_template()
    canvas = tpl.get("canvas", {"width": 1700, "height": 2400})
    warped, _pts, method, _c_ids, _dict, status, _reg = detect_corners_and_crop(
        img_bgr, canvas_w=canvas["width"], canvas_h=canvas["height"], preferred_method="aruco",
        expected_ids=tpl.get("aruco_corner_ids"), dict_name=tpl.get("aruco_dict", "DICT_4X4_50"), crop_mode="inner")
    if warped is None:
        raise HTTPException(422, f"Sudut LJK tak terdeteksi di foto ini ({status}) -- coba foto lain yg lebih jelas/rata")
    ok, buf = cv2.imencode(".jpg", warped, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise HTTPException(422, "Gagal menyimpan hasil crop")
    token = uuid.uuid4().hex
    with open(os.path.join(_calib_tmp_dir(), f"{token}.jpg"), "wb") as f:
        f.write(buf.tobytes())
    return {"token": token, "canvas": canvas, "method": method}


@router.get("/calib/preview")
def calib_preview(token: str, field: str = "", dx: float = 0.0, dy: float = 0.0, db=Depends(get_db)):
    """Gambar ulang kotak template di atas hasil crop yg dicache dari /calib/upload. Koreksi TERSIMPAN
    dipakai utk semua blok, KECUALI `field` (kalau diisi) yg memakai dx/dy dari slider -- pratinjau
    langsung, BELUM disimpan (simpan lewat PUT /calib/fields/{field}). `field` kosong = cuma tampilkan
    garis besar semua blok, tak ada yg ditonjolkan."""
    path = os.path.join(_calib_tmp_dir(), f"{token}.jpg")
    if not os.path.exists(path):
        raise HTTPException(410, "Foto referensi sudah kedaluwarsa (>2 jam) -- unggah lagi")
    warped = cv2.imread(path)
    if warped is None:
        raise HTTPException(422, "Gagal membaca foto referensi")
    tpl = _calib_template()
    calib = {c.field_name: (c.dx, c.dy) for c in db.scalars(select(TemplateCalib))}
    if field:
        calib = {**calib, field: (dx, dy)}
    fields_dict = apply_field_calib(tpl.get("fields", {}), calib)
    img = _draw_calib_overlay(warped, fields_dict, selected=field or None)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise HTTPException(422, "Gagal membuat pratinjau")
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- akun admin (tab Akun)
def _user_view(u: AdminUser) -> dict:
    return {"id": u.id, "username": u.username, "name": u.name, "hp": u.hp, "active": u.active,
            "created_at": u.created_at.isoformat() if u.created_at else None, "created_by": u.created_by,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None, "type": u.type or "admin", "sso_only": not u.password_hash}


@router.get("/whoami")
def admin_whoami(admin=Depends(auth.require_admin)):
    """Identitas admin yg sedang login + apakah super user (menu Akun) -- dipakai UI utk menyembunyikan tab."""
    return {"name": admin.get("name"), "email": admin.get("email"), "superuser": auth.is_superuser(admin)}


@router.get("/users", dependencies=[Depends(auth.require_superuser)])
def admin_list_users(db=Depends(get_db)):
    """Daftar akun admin yg dikelola lewat DB (tab Akun di menu admin) -- password tak pernah disertakan.
    TAK termasuk ADMIN_USER/ADMIN_ACCOUNTS dari env (deploy/.env): itu akun bawaan/cadangan yg cuma bisa
    diubah lewat redeploy, sengaja tak ditampilkan di sini krn admin tak bisa mengelolanya dari UI ini."""
    return [_user_view(u) for u in db.scalars(select(AdminUser).order_by(AdminUser.created_at))]


class _AdminUserIn(BaseModel):
    username: str
    password: str
    name: str = ""
    hp: str = ""


@router.post("/users", dependencies=[Depends(auth.require_superuser)])
def admin_create_user(body: _AdminUserIn, db=Depends(get_db), admin=Depends(auth.require_admin)):
    """Buat akun admin baru -- aktif seketika, tanpa redeploy (beda dgn ADMIN_ACCOUNTS di env)."""
    username = body.username.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,100}", username):
        raise HTTPException(422, "Username 3-100 karakter: huruf/angka/._- saja")
    if len(body.password) < 8:
        raise HTTPException(422, "Password minimal 8 karakter")
    if username in auth._admin_accounts():
        raise HTTPException(409, "Username sudah dipakai")
    u = AdminUser(username=username, password_hash=auth.hash_password(body.password), name=body.name.strip(),
                  hp=body.hp.strip(), created_by=admin.get("name") or admin.get("email") or "?")
    db.add(u)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 -- race kecil kemungkinan (dua permintaan bareng, username sama)
        db.rollback()
        raise HTTPException(409, "Username sudah dipakai")
    return _user_view(u)


class _AdminUserPatchIn(BaseModel):
    password: str | None = None
    name: str | None = None
    hp: str | None = None
    active: bool | None = None


class _AdminEmailIn(BaseModel):
    email: str
    name: str = ""


@router.post("/users/email", dependencies=[Depends(auth.require_superuser)])
def admin_create_email_user(body: _AdminEmailIn, db=Depends(get_db), admin=Depends(auth.require_admin)):
    """Daftarkan email Google/Microsoft yg boleh masuk admin (akun SSO-saja: username = email, tanpa password).
    Aktif seketika, tanpa restart (auth.is_allowed membaca tabel ini). Selalu bertipe admin biasa."""
    email = body.email.strip().lower()
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", email) or len(email) > 100:
        raise HTTPException(422, "Format email tidak valid")
    if email in config.ADMIN_EMAILS or db.scalar(select(AdminUser).where(AdminUser.username == email)):
        raise HTTPException(409, "Email sudah terdaftar")
    u = AdminUser(username=email, password_hash="", name=body.name.strip() or email,
                  created_by=admin.get("name") or admin.get("email") or "?")
    db.add(u)
    db.commit()
    return _user_view(u)


@router.patch("/users/{uid}", dependencies=[Depends(auth.require_superuser)])
def admin_update_user(uid: str, body: _AdminUserPatchIn, db=Depends(get_db)):
    """Reset password, ubah nama, dan/atau nonaktifkan (active=false) -- tanpa menghapus riwayat akunnya."""
    u = db.get(AdminUser, uid)
    if u is None:
        raise HTTPException(404, "Akun tidak ditemukan")
    if body.password is not None:
        if not u.password_hash:
            raise HTTPException(409, "Akun email hanya bisa masuk lewat Google/Microsoft (tanpa password)")
        if len(body.password) < 8:
            raise HTTPException(422, "Password minimal 8 karakter")
        u.password_hash = auth.hash_password(body.password)
    if body.name is not None:
        u.name = body.name.strip()
    if body.hp is not None:
        u.hp = body.hp.strip()
    if body.active is not None:
        u.active = body.active
    db.commit()
    return _user_view(u)


@router.delete("/users/{uid}", status_code=204, dependencies=[Depends(auth.require_superuser)])
def admin_delete_user(uid: str, db=Depends(get_db)):
    u = db.get(AdminUser, uid)
    if u is None:
        raise HTTPException(404, "Akun tidak ditemukan")
    db.delete(u)
    db.commit()


@router.get("/access-log", dependencies=[Depends(auth.require_superuser)])
def admin_access_log(limit: int = Query(100, ge=1, le=500), db=Depends(get_db)):
    """Riwayat percobaan masuk admin terbaru (berhasil & ditolak), lintas SSO Google/Microsoft & password
    -- lihat server.db.AdminAccessLog. Dipakai tab Akun bag. "Riwayat akses"."""
    rows = db.scalars(select(AdminAccessLog).order_by(AdminAccessLog.at.desc()).limit(limit))
    return [{"id": r.id, "identity": r.identity, "method": r.method, "success": r.success,
             "reason": r.reason, "ip": r.ip, "at": r.at.isoformat() if r.at else None} for r in rows]
