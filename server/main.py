"""API pemindaian LJK (FastAPI). Pemindaian berjalan di process pool, bukan di event loop."""
import csv
import io
import os
import re
import shutil
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile as FUploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from scanner.service import FAKULTAS_PRODI, classify_scan_status, is_valid_phone
from server import config, worker
from server.db import Kunci, ScanSession, Sheet, SessionLocal, UploadFile, init_db

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
_pool: ProcessPoolExecutor | None = None


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name or "berkas"))[:120] or "berkas"


def _kunci(db):
    return {k.name: k.data for k in db.scalars(select(Kunci))}


def _pengawas(s: ScanSession):
    return {"nama": s.nama_pengawas, "hp": s.hp, "ruangan": s.ruangan, "fakultas": s.fakultas, "prodi": s.prodi}


# --------------------------------------------------------------------------- antrian
def _enqueue(file_id):
    with SessionLocal() as db:
        f = db.get(UploadFile, file_id)
        s = db.get(ScanSession, f.session_id)
        f.state = "processing"
        args = (f.path, f.name, _pengawas(s), _kunci(db))
        db.commit()
    fut = _pool.submit(worker.scan_file, *args)
    fut.add_done_callback(lambda fu, fid=file_id: _on_done(fid, fu))


def _on_done(file_id, fut):
    try:
        results = fut.result()
        err = None
    except Exception as e:  # noqa: BLE001 — kegagalan satu berkas tidak boleh mematikan antrian
        results, err = [], f"{type(e).__name__}: {e}"
    with SessionLocal() as db:
        f = db.get(UploadFile, file_id)
        if f is None:  # sesi dihapus saat diproses
            return
        seq = len(db.scalars(select(Sheet.id).where(Sheet.session_id == f.session_id)).all())
        for r in results:
            seq += 1
            db.add(Sheet(session_id=f.session_id, file_id=f.id, page=r["page"], seq=seq,
                         doc_name=r["doc_name"], scan_status=r["status"], record=r["record"]))
        f.state = "failed" if err else "done"
        f.error = err
        db.commit()


@asynccontextmanager
async def lifespan(app):
    global _pool
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    init_db()
    _pool = ProcessPoolExecutor(max_workers=config.SCAN_WORKERS)
    list(_pool.map(worker.warmup, range(config.SCAN_WORKERS)))  # muat template sekali per proses
    with SessionLocal() as db:  # pulihkan pekerjaan yang terputus saat restart
        pending = [f.id for f in db.scalars(select(UploadFile).where(UploadFile.state.in_(("queued", "processing"))))]
    for fid in pending:
        _enqueue(fid)
    yield
    _pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="LJK Scanner API", lifespan=lifespan)


def get_db():
    with SessionLocal() as db:
        yield db


def _need_admin(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Token admin tidak valid")


# --------------------------------------------------------------------------- meta & sesi
@app.get("/api/meta")
def meta():
    return {"fakultas_prodi": FAKULTAS_PRODI, "max_upload_mb": config.MAX_UPLOAD_MB,
            "extensions": sorted(config.ALLOWED_EXT)}


class SessionIn(BaseModel):
    nama_pengawas: str
    hp: str
    ruangan: str
    fakultas: str
    prodi: str


@app.post("/api/sessions", status_code=201)
def create_session(body: SessionIn, db=Depends(get_db)):
    errors = []
    if not body.nama_pengawas.strip():
        errors.append("Nama lengkap pengawas wajib diisi")
    if not is_valid_phone(body.hp):
        errors.append("Nomor HP tidak valid")
    if not body.ruangan.strip():
        errors.append("Ruangan wajib diisi")
    if body.fakultas not in FAKULTAS_PRODI:
        errors.append("Fakultas tidak valid")
    elif body.prodi not in FAKULTAS_PRODI[body.fakultas]:
        errors.append("Program studi tidak sesuai fakultas")
    if errors:
        raise HTTPException(422, errors)
    s = ScanSession(nama_pengawas=body.nama_pengawas.strip(), hp=body.hp.strip(), ruangan=body.ruangan.strip(),
                    fakultas=body.fakultas, prodi=body.prodi)
    db.add(s)
    db.commit()
    return {"id": s.id}


def _session_or_404(db, sid):
    s = db.get(ScanSession, sid)
    if s is None:
        raise HTTPException(404, "Sesi tidak ditemukan")
    return s


def _sheet_view(sh: Sheet):
    r = sh.record
    return {
        "id": sh.id, "seq": sh.seq, "file": r.get("File"),
        "nama": r.get("Nama Mahasiswa"), "npm": r.get("NPM"), "kode_soal": r.get("Kode Soal"),
        "fakultas_ljk": r.get("Fakultas (LJK)"), "terisi": r.get("Jawaban Terisi"),
        "label": classify_scan_status({"status": sh.scan_status}, sh.validated),
        "validated": sh.validated,
    }


@app.get("/api/sessions/{sid}")
def get_session(sid: str, db=Depends(get_db)):
    s = _session_or_404(db, sid)
    files = s.files
    pending = sum(1 for f in files if f.state in ("queued", "processing"))
    sheets = [_sheet_view(x) for x in s.sheets]
    return {
        "id": s.id, "pengawas": _pengawas(s), "submitted": s.submitted,
        "files": {"total": len(files), "pending": pending, "failed": [
            {"name": f.name, "error": f.error} for f in files if f.state == "failed"]},
        "scanning": pending > 0,
        "sheets": sheets,
        "summary": {"lembar": len(sheets), "ok": sum(1 for x in sheets if x["label"] == "OK"),
                    "perlu_validasi": sum(1 for x in sheets if x["label"] == "Perlu Validasi"),
                    "gagal": sum(1 for x in sheets if x["label"] == "Gagal")},
    }


# --------------------------------------------------------------------------- upload
async def _save_stream(up: FUploadFile, dest: str) -> int:
    """Tulis upload ke disk per potongan (RAM konstan) dan tolak bila melebihi batas."""
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with open(dest, "wb") as out:
        while chunk := await up.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                os.remove(dest)
                raise HTTPException(413, f"{up.filename}: melebihi {config.MAX_UPLOAD_MB} MB")
            out.write(chunk)
    return size


def _check_ext(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in config.ALLOWED_EXT:
        raise HTTPException(415, f"{name}: tipe berkas tidak didukung")


@app.post("/api/sessions/{sid}/files", status_code=202)
async def upload_files(sid: str, files: list[FUploadFile] = File(...), db=Depends(get_db)):
    s = _session_or_404(db, sid)
    if s.submitted:
        raise HTTPException(409, "Sesi sudah disubmit")
    folder = os.path.join(config.UPLOAD_DIR, s.id)
    os.makedirs(folder, exist_ok=True)
    ids = []
    for up in files:
        _check_ext(up.filename or "")
        rec = UploadFile(session_id=s.id, name=up.filename or "berkas", size=0, path="")
        rec.path = os.path.join(folder, f"{uuid.uuid4().hex[:8]}_{_safe_name(up.filename)}")
        rec.size = await _save_stream(up, rec.path)
        db.add(rec)
        db.commit()
        _enqueue(rec.id)
        ids.append(rec.id)
    return {"queued": ids}


# --------------------------------------------------------------------------- validasi
def _sheet_or_404(db, shid):
    sh = db.get(Sheet, shid)
    if sh is None:
        raise HTTPException(404, "Lembar tidak ditemukan")
    return sh


def _guard_open(db, sh):
    if db.get(ScanSession, sh.session_id).submitted:
        raise HTTPException(409, "Sesi sudah disubmit")


@app.post("/api/sheets/{shid}/validate")
def validate(shid: str, db=Depends(get_db)):
    sh = _sheet_or_404(db, shid)
    _guard_open(db, sh)
    if classify_scan_status({"status": sh.scan_status}, False) == "Gagal":
        raise HTTPException(409, "Lembar gagal terbaca — ganti foto dulu")
    sh.validated = True
    db.commit()
    return _sheet_view(sh)


@app.post("/api/sheets/{shid}/unvalidate")
def unvalidate(shid: str, db=Depends(get_db)):
    sh = _sheet_or_404(db, shid)
    _guard_open(db, sh)
    sh.validated = False
    db.commit()
    return _sheet_view(sh)


@app.post("/api/sessions/{sid}/validate-all")
def validate_all(sid: str, value: bool = True, db=Depends(get_db)):
    s = _session_or_404(db, sid)
    if s.submitted:
        raise HTTPException(409, "Sesi sudah disubmit")
    for sh in s.sheets:
        if not value:
            sh.validated = False
        elif classify_scan_status({"status": sh.scan_status}, False) != "Gagal":
            sh.validated = True
    db.commit()
    return {"ok": True}


@app.delete("/api/sheets/{shid}", status_code=204)
def delete_sheet(shid: str, db=Depends(get_db)):
    sh = _sheet_or_404(db, shid)
    _guard_open(db, sh)
    db.delete(sh)
    db.commit()


@app.post("/api/sheets/{shid}/replace")
async def replace_photo(shid: str, file: FUploadFile = File(...), db=Depends(get_db)):
    sh = _sheet_or_404(db, shid)
    _guard_open(db, sh)
    s = db.get(ScanSession, sh.session_id)
    _check_ext(file.filename or "")
    dest = os.path.join(config.UPLOAD_DIR, s.id, f"{uuid.uuid4().hex[:8]}_{_safe_name(file.filename)}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    size = await _save_stream(file, dest)
    fut = _pool.submit(worker.scan_file, dest, file.filename, _pengawas(s), _kunci(db), 0)
    try:
        res = fut.result(timeout=120)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Gagal memproses foto baru: {e}")
    if not res:
        raise HTTPException(422, "Foto baru tidak berisi halaman")
    up = db.get(UploadFile, sh.file_id)
    up.path, up.name, up.size = dest, file.filename, size
    sh.page, sh.doc_name, sh.scan_status, sh.record, sh.validated = 0, res[0]["doc_name"], res[0]["status"], res[0]["record"], False
    db.commit()
    return _sheet_view(sh)


@app.get("/api/sheets/{shid}/preview")
def preview(shid: str, db=Depends(get_db)):
    """Preview on-demand: gambar hasil preprocessing + bubble terbaca (tidak disimpan)."""
    sh = _sheet_or_404(db, shid)
    s = db.get(ScanSession, sh.session_id)
    up = db.get(UploadFile, sh.file_id)
    if not os.path.exists(up.path):
        raise HTTPException(410, "Berkas sumber sudah dihapus")
    fut = _pool.submit(worker.scan_file, up.path, up.name, _pengawas(s), _kunci(db), sh.page, True)
    res = fut.result(timeout=120)
    if not res or "overlay_jpeg" not in res[0]:
        raise HTTPException(404, "Preview tidak tersedia")
    return Response(res[0]["overlay_jpeg"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/sessions/{sid}/submit")
def submit(sid: str, db=Depends(get_db)):
    s = _session_or_404(db, sid)
    if s.submitted:
        return {"ok": True, "already": True}
    if any(f.state in ("queued", "processing") for f in s.files):
        raise HTTPException(409, "Pemindaian masih berjalan")
    if not s.sheets:
        raise HTTPException(409, "Belum ada lembar")
    bad = [x.id for x in s.sheets if classify_scan_status({"status": x.scan_status}, x.validated) != "OK"]
    if bad:
        raise HTTPException(409, f"{len(bad)} lembar belum tervalidasi")
    from datetime import datetime, timezone
    s.submitted, s.submitted_at = True, datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "lembar": len(s.sheets)}


# --------------------------------------------------------------------------- admin
@app.put("/api/admin/kunci/{name}", dependencies=[Depends(_need_admin)])
def put_kunci(name: str, data: dict, db=Depends(get_db)):
    k = db.get(Kunci, name)
    if k is None:
        db.add(Kunci(name=name, data=data))
    else:
        k.data = data
    db.commit()
    return {"ok": True, "soal": len(data)}


@app.get("/api/admin/export.csv", dependencies=[Depends(_need_admin)])
def export_csv(db=Depends(get_db)):
    rows = []
    for sh in db.scalars(select(Sheet).join(ScanSession).where(ScanSession.submitted.is_(True)).order_by(Sheet.session_id, Sheet.seq)):
        rows.append(sh.record)
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=rekap_ljk.csv"})


@app.get("/api/health")
def health():
    return {"ok": True, "workers": config.SCAN_WORKERS}
