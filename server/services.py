"""Layanan latar: sinkron Google Sheet (outbox), sinkron kunci jawaban, pembersihan berkas, penilaian ulang."""
import datetime as dt
import logging
import os
import shutil
import uuid

from sqlalchemy import select

from core.evaluator import grade_student_record
from server import config, sheets
from server.db import Kunci, ScanSession, SessionLocal, UploadFile

log = logging.getLogger("uvicorn.error")
STATE = {"sync_last": None, "sync_last_error": None, "kunci_last": None, "kunci_error": None, "cleanup_last": None}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def kunci_int(db):
    """Kunci jawaban dari DB dengan nomor soal bertipe int (format yang diharapkan modul penilaian)."""
    return {k.name: {int(q): a for q, a in k.data.items()} for k in db.scalars(select(Kunci))}


def regrade_session(db, s: ScanSession):
    """Hitung ulang nilai semua lembar dengan kunci TERBARU (kunci bisa diunggah setelah pemindaian)."""
    k = kunci_int(db)
    if not k:
        return 0
    for sh in s.sheets:
        sh.record = grade_student_record(dict(sh.record), k)
    return len(s.sheets)


GRADE_FIELDS = ("Nilai", "Jumlah Benar", "Jumlah Salah", "Jumlah Kosong", "Kunci Terpakai", "Jawaban Terisi")


def regrade_all(kelas: str = "", only_changed_report: bool = True):
    """Hitung ulang nilai SEMUA lembar pada sesi yang sudah disubmit memakai kunci terbaru di DB.

    Mengembalikan ringkasan: berapa lembar berubah, dan berapa sesi berubah yang barisnya SUDAH terkirim ke
    Google Sheet (baris di Sheet itu kini usang; unduh ulang dari admin atau tulis ulang snapshot)."""
    out = {"sessions": 0, "sheets": 0, "changed": 0, "changed_sessions": 0, "changed_in_synced": 0, "no_key": 0}
    with SessionLocal() as db:
        k = kunci_int(db)
        if not k:
            return {**out, "error": "Belum ada kunci jawaban"}
        q = select(ScanSession).where(ScanSession.submitted.is_(True))
        if kelas:
            q = q.where(ScanSession.kelas == kelas)
        for s in db.scalars(q):
            out["sessions"] += 1
            changed_here = 0
            for sh in s.sheets:
                out["sheets"] += 1
                before = {f: sh.record.get(f) for f in GRADE_FIELDS}
                rec = grade_student_record(dict(sh.record), k)
                if rec.get("Kunci Terpakai") == "Tidak Ditemukan":
                    out["no_key"] += 1
                if {f: rec.get(f) for f in GRADE_FIELDS} != before:
                    sh.record = rec
                    changed_here += 1
            if changed_here:
                out["changed"] += changed_here
                out["changed_sessions"] += 1
                if s.synced_at is not None:
                    out["changed_in_synced"] += 1
        db.commit()
    return out


# --------------------------------------------------------------------------- sinkron rekap
def _backoff(attempts: int) -> dt.timedelta:
    return dt.timedelta(seconds=min(15 * 2 ** attempts, 3600))


def sync_pending_once(limit: int = 30):
    """Kirim sesi yang sudah disubmit tapi belum tersinkron ke Google Sheet.

    Semua sesi yang jatuh tempo digabung menjadi SATU permintaan batch (hemat kuota API). Bila batch gagal,
    dicoba per sesi supaya satu sesi bermasalah tidak menahan yang lain."""
    client = sheets.get_client()
    if client is None:
        return {"configured": False, "synced": 0}
    now = _now()
    cond = [ScanSession.submitted.is_(True), ScanSession.synced_at.is_(None),
            ScanSession.sync_attempts < config.SYNC_MAX_ATTEMPTS,
            (ScanSession.sync_next.is_(None)) | (ScanSession.sync_next <= now)]
    if config.SYNC_SINCE:   # abaikan sesi lama (mis. data uji) sebelum batas waktu ini
        cond.append(ScanSession.submitted_at >= dt.datetime.fromisoformat(config.SYNC_SINCE.replace("Z", "+00:00")))
    with SessionLocal() as db:
        due = list(db.scalars(select(ScanSession).where(*cond).order_by(ScanSession.submitted_at).limit(limit)))
        if not due:
            return {"configured": True, "synced": 0}

        def _ship(group):
            recs = [dict(sh.record) for s in group for sh in s.sheets]
            client.append_records(recs)
            for s in group:
                s.synced_at, s.sync_error, s.sync_next = _now(), None, None

        def _fail(s, e):
            s.sync_attempts = (s.sync_attempts or 0) + 1
            s.sync_error = f"{type(e).__name__}: {e}"[:500]
            s.sync_next = _now() + _backoff(s.sync_attempts)

        try:
            _ship(due)
        except Exception as e:  # noqa: BLE001
            if len(due) == 1:
                _fail(due[0], e)
            else:
                for s in due:
                    try:
                        _ship([s])
                    except Exception as e2:  # noqa: BLE001
                        _fail(s, e2)
        db.commit()
        done = sum(1 for s in due if s.synced_at)
        STATE["sync_last"] = _now().isoformat()
        STATE["sync_last_error"] = next((s.sync_error for s in due if s.sync_error), None)
        return {"configured": True, "synced": done, "failed": len(due) - done}


def sync_session_now(db, s: ScanSession) -> dict:
    """Kirim SATU sesi ke Google Sheet sekarang juga -- tombol "Kirim" manual di admin (tak menunggu
    jadwal berkala sync_pending_once). UPSERT per NPM (GSheetsClient.upsert_records): baris yg NPM-nya
    SUDAH ada di Sheet diperbarui di tempat, yg belum ada ditambahkan baru -- jadi AMAN dipanggil
    berulang kali, termasuk utk sesi yg sudah PERNAH tersinkron sebelumnya (mis. stlh admin mengoreksi
    data lewat bulk-edit/edit lembar & ingin Sheet ikut diperbarui), beda dgn sync_pending_once yg pakai
    append_records polos (append pernah menggandakan baris kalau dipanggil 2x utk sesi yg sama)."""
    client = sheets.get_client()
    if client is None:
        return {"ok": False, "error": "Google Sheet belum dikonfigurasi"}
    if not s.submitted:
        return {"ok": False, "error": "Sesi belum disubmit/divalidasi"}
    try:
        recs = [dict(sh.record) for sh in s.sheets]
        res = client.upsert_records(recs, key_col="NPM")
        s.synced_at, s.sync_error, s.sync_next, s.sync_attempts = _now(), None, None, 0
    except Exception as e:  # noqa: BLE001
        s.sync_attempts = (s.sync_attempts or 0) + 1
        s.sync_error = f"{type(e).__name__}: {e}"[:500]
        s.sync_next = _now() + _backoff(s.sync_attempts)
        db.commit()
        return {"ok": False, "error": s.sync_error}
    db.commit()
    STATE["sync_last"] = _now().isoformat()
    return {"ok": True, "updated": res["updated"], "appended": res["appended"]}


# --------------------------------------------------------------------------- kunci jawaban
def upsert_kunci(db, name: str, data: dict, source: str):
    norm = {str(int(q)): str(a).strip() for q, a in data.items()}
    k = db.get(Kunci, name)
    if k is None:
        db.add(Kunci(name=name, data=norm, source=source, updated_at=_now()))
    else:
        k.data, k.source, k.updated_at = norm, source, _now()


def sync_kunci_once():
    """Tarik kunci jawaban dari Google Sheet ke DB (tidak menghapus kunci yang tidak ada di Sheet)."""
    client = sheets.get_client()
    if client is None:
        return {"configured": False, "kunci": 0}
    try:
        found = client.fetch_kunci()
        with SessionLocal() as db:
            for name, q in found.items():
                upsert_kunci(db, name, q, "gsheet")
            db.commit()
        STATE["kunci_last"], STATE["kunci_error"] = _now().isoformat(), None
        return {"configured": True, "kunci": len(found)}
    except Exception as e:  # noqa: BLE001
        STATE["kunci_error"] = f"{type(e).__name__}: {e}"[:300]
        log.warning("sync kunci gagal: %s", STATE["kunci_error"])
        return {"configured": True, "kunci": 0, "error": STATE["kunci_error"]}


# --------------------------------------------------------------------------- pembersihan
def remove_session_files(s) -> int:
    """Hapus berkas sumber milik SESI INI saja. Foto kini disimpan per Fakultas/Prodi/Kelas (dibagi antar
    sesi yg sama kombinasinya -- lihat server/main.py _class_folder), jadi TIDAK boleh rmtree seluruh folder
    kelas (bisa ikut menghapus punya sesi lain) -- hapus file per file sesuai UploadFile.path sesi ini."""
    n = 0
    for f in s.files:
        if f.path and os.path.exists(f.path):
            try:
                os.remove(f.path)
                n += 1
            except OSError:
                pass
    return n


def session_has_photos(s) -> bool:
    return any(f.path and os.path.exists(f.path) for f in s.files)


def migrate_old_storage(db) -> dict:
    """Pindahkan berkas sesi LAMA (dari sebelum foto ditata per Fakultas/Prodi/Kelas -- folder waktu itu
    cuma UPLOAD_DIR/{session_id}/..., lihat server/main.py _class_folder) ke struktur baru, & perbarui
    UploadFile.path supaya cocok. Aman dipanggil berkali-kali (idempoten): berkas yg foldernya SUDAH
    sesuai (mis. diunggah setelah migrasi ini ada) dilewati begitu saja, bukan dipindah ulang. Folder
    lama yg jadi kosong ikut dihapus. Kembalikan ringkasan; tak pernah menghapus berkas tanpa memastikan
    salinannya di lokasi baru berhasil dulu (shutil.move: gagal -> berkas lama TETAP di path asalnya,
    UploadFile.path juga TAK diubah, jadi aman dicoba lagi lain waktu)."""
    from server import main as _main
    moved = ok = missing = failed = 0
    errors = []
    touched_dirs = set()
    for up in db.scalars(select(UploadFile)):
        if not up.path:
            continue
        s = db.get(ScanSession, up.session_id)
        if s is None:
            continue
        expected_dir = _main._class_folder(s)
        cur_dir = os.path.dirname(up.path)
        if os.path.normpath(cur_dir) == os.path.normpath(expected_dir):
            ok += 1
            continue
        if not os.path.exists(up.path):
            missing += 1
            continue
        touched_dirs.add(cur_dir)
        os.makedirs(expected_dir, exist_ok=True)
        base = os.path.basename(up.path)
        dest = os.path.join(expected_dir, base)
        if os.path.exists(dest):   # tabrakan nama nyaris mustahil (nama diberi prefiks acak saat unggah) -- tetap dijaga
            dest = os.path.join(expected_dir, f"{uuid.uuid4().hex[:8]}_{base}")
        try:
            shutil.move(up.path, dest)
        except OSError as e:  # noqa: BLE001 — satu berkas gagal dipindah tak boleh menghentikan sisanya
            failed += 1
            errors.append(f"{up.id}: {e}")
            continue
        up.path = dest
        moved += 1
    db.commit()
    for d in touched_dirs:   # bersihkan folder lama (per-session-id) yg sudah kosong
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass
    return {"dipindah": moved, "sudah_benar": ok, "hilang": missing, "gagal": failed, "errors": errors[:20]}


def cleanup_once():
    """Hapus berkas sumber sesi yang sudah lama (sudah disubmit: UPLOAD_RETENTION_HOURS; belum: UNSUBMITTED_RETENTION_HOURS).
    Dimatikan sepenuhnya bila CLEANUP_ENABLED=0 (lihat server/config.py) -- dipakai saat admin masih perlu
    mengecek foto asli, tanpa risiko keburu terhapus otomatis."""
    if not config.CLEANUP_ENABLED:
        return {"files_removed": 0, "disabled": True}
    now = _now()
    cut_sub = now - dt.timedelta(hours=config.UPLOAD_RETENTION_HOURS)
    cut_open = now - dt.timedelta(hours=config.UNSUBMITTED_RETENTION_HOURS)
    removed = 0
    with SessionLocal() as db:
        old = list(db.scalars(select(ScanSession).where(
            ((ScanSession.submitted.is_(True)) & (ScanSession.submitted_at < cut_sub))
            | ((ScanSession.submitted.is_(False)) & (ScanSession.created_at < cut_open)))))
        for s in old:
            removed += remove_session_files(s)
    STATE["cleanup_last"] = now.isoformat()
    return {"files_removed": removed}
