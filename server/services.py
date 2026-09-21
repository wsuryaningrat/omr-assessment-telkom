"""Layanan latar: sinkron Google Sheet (outbox), sinkron kunci jawaban, pembersihan berkas, penilaian ulang."""
import datetime as dt
import logging
import os
import shutil

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
    with SessionLocal() as db:
        due = list(db.scalars(
            select(ScanSession).where(
                ScanSession.submitted.is_(True), ScanSession.synced_at.is_(None),
                ScanSession.sync_attempts < config.SYNC_MAX_ATTEMPTS,
                (ScanSession.sync_next.is_(None)) | (ScanSession.sync_next <= now),
            ).order_by(ScanSession.submitted_at).limit(limit)))
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
def cleanup_once():
    """Hapus berkas sumber sesi yang sudah lama (sudah disubmit: UPLOAD_RETENTION_HOURS; belum: UNSUBMITTED_RETENTION_HOURS)."""
    now = _now()
    cut_sub = now - dt.timedelta(hours=config.UPLOAD_RETENTION_HOURS)
    cut_open = now - dt.timedelta(hours=config.UNSUBMITTED_RETENTION_HOURS)
    removed = 0
    with SessionLocal() as db:
        old = list(db.scalars(select(ScanSession).where(
            ((ScanSession.submitted.is_(True)) & (ScanSession.submitted_at < cut_sub))
            | ((ScanSession.submitted.is_(False)) & (ScanSession.created_at < cut_open)))))
        for s in old:
            folder = os.path.join(config.UPLOAD_DIR, s.id)
            if os.path.isdir(folder):
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
    STATE["cleanup_last"] = now.isoformat()
    return {"folders_removed": removed}
