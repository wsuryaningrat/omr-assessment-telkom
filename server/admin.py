"""Endpoint admin (header X-Admin-Token): ringkasan, sesi, kunci jawaban, sinkron Google Sheet, ekspor."""
import csv
import datetime as dt
import io
import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile as FUploadFile
from fastapi.responses import Response
from sqlalchemy import Integer, func, select

from core.evaluator import parse_kunci_jawaban_raw_rows
from server import auth, config, plotting, services, sheets
from server.db import Kunci, ScanSession, Sheet, SessionLocal


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
    total = db.scalar(select(func.count()).select_from(ScanSession).where(*cond)) or 0
    rows = db.scalars(select(ScanSession).where(*cond).order_by(ScanSession.created_at.desc()).offset((page - 1) * size).limit(size))
    items = []
    for s in rows:
        items.append({
            "id": s.id, "nama": s.nama_pengawas, "hp": s.hp, "kelas": s.kelas,
            "prodi": s.prodi, "lembar": len(s.sheets),
            "validated": sum(1 for x in s.sheets if x.validated), "submitted": s.submitted,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
            "synced": s.synced_at is not None, "sync_attempts": s.sync_attempts or 0, "sync_error": s.sync_error,
        })
    return {"total": total, "page": page, "size": size, "items": items}


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
