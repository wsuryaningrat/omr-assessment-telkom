"""API pemindaian LJK (FastAPI). Pemindaian berjalan di process pool, bukan di event loop."""
import asyncio
import multiprocessing
import logging
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile as FUploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, select

from scanner.service import classify_scan_status, load_default_template
from server import refdata, admin, auth, config, plotting, services, worker
from server.db import Kunci, ScanSession, Sheet, SessionLocal, UploadFile, init_db

_pool: ProcessPoolExecutor | None = None
_pending = multiprocessing.Value("i", 0)   # pekerjaan pindai berjalan + mengantre; dibaca worker untuk menentukan jumlah thread
_FUTURES = {}   # file_id -> Future, hanya utk yg masih 'queued'/'processing' (lihat cancel_pending_files)
_RESCAN_FUTURES = {}    # sheet_id -> Future, khusus pindai-ulang massal (lihat _enqueue_rescan)
_RESCAN_PENDING = {}    # session_id -> jumlah lembar yg masih diantre/diproses pindai-ulang massal (utk progres di UI)
_rescan_lock = threading.Lock()


def _submit_scan(*args, **kwargs):
    with _pending.get_lock():
        _pending.value += 1
    try:
        fut = _pool.submit(worker.scan_file, *args, **kwargs)
    except Exception:
        _scan_finished(None)
        raise
    fut.add_done_callback(_scan_finished)
    return fut


def _scan_finished(_fut):
    with _pending.get_lock():
        _pending.value = max(0, _pending.value - 1)


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name or "berkas"))[:120] or "berkas"


def _safe_segment(name):
    """Satu ruas path aman (fakultas/prodi/kelas) -- tanpa "/" atau "..", panjang wajar."""
    s = re.sub(r"[^A-Za-z0-9._ -]+", "_", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")[:80]
    return s or "-"


def _class_folder(s: ScanSession) -> str:
    """Folder foto sumber: UPLOAD_DIR/Fakultas/Prodi/Kelas -- DIBAGI antar sesi dari fakultas/prodi/kelas
    yg sama (bukan per-sesi lagi), supaya foto tertata per kelas & gampang dicek admin di disk. Nama berkas
    di dalamnya tetap diberi prefiks acak (lihat upload_files/replace_photo) jadi aman dari tabrakan nama;
    yg TIDAK aman dari tabrakan adalah makna "satu kelas = satu sesi" -- lihat /api/kelas-check &
    services.remove_session_files (hapus per-berkas milik sesi ini saja, bukan rmtree seluruh folder)."""
    return os.path.join(config.UPLOAD_DIR, _safe_segment(s.fakultas), _safe_segment(s.prodi), _safe_segment(s.kelas))


def _scan_cache_path(sheet_id: str) -> str:
    """Lokasi cache JPEG 'Hasil scan' (overlay bulatan terbaca) satu lembar -- lihat preview(). Dikunci ke
    sheet_id (stabil, tak ikut berubah saat folder Fakultas/Prodi/Kelas direorganisasi), BUKAN di bawah
    _class_folder(s)."""
    return os.path.join(config.UPLOAD_DIR, ".scan_cache", f"{sheet_id}.jpg")


def _save_scan_cache(sheet_id: str, jpeg_bytes: bytes):
    path = _scan_cache_path(sheet_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(jpeg_bytes)


def _clear_scan_cache(sheet_id: str):
    """Hapus cache 'Hasil scan' lembar ini (dipanggil tiap kali hasilnya mungkin berubah: pindai ulang,
    ganti foto, lembar dihapus, atau sesi sudah 'Sent' & tak perlu dilihat lagi -- lihat services.py)."""
    try:
        os.remove(_scan_cache_path(sheet_id))
    except OSError:
        pass


def _delete_sheet_and_cleanup(db, sh):
    """Hapus satu Sheet (LJK) -- SEKALIGUS hapus UploadFile & foto sumbernya dari disk, bila setelah ini
    tak ada Sheet LAIN yg masih memakai berkas itu (satu UploadFile bisa dipakai beberapa Sheet utk PDF
    multi-halaman -- lihat Sheet.file_id). Tanpa ini, menghapus lembar meninggalkan UploadFile.state='done'
    tanpa Sheet sama sekali -- PERSIS kondisi yg dideteksi admin._orphan_files sbg "hilang senyap" (dulu
    dirancang utk berkas yg KEBETULAN gagal tercatat krn restart server, bukan yg SENGAJA admin hapus),
    jadi lembar yg dihapus sengaja malah muncul lagi sbg peringatan "berkas tak menghasilkan lembar"."""
    sheet_id, file_id = sh.id, sh.file_id
    db.delete(sh)
    db.flush()
    remaining = db.scalar(select(func.count()).select_from(Sheet).where(Sheet.file_id == file_id))
    if not remaining:
        up = db.get(UploadFile, file_id)
        if up is not None:
            if up.path and os.path.exists(up.path):
                try:
                    os.remove(up.path)
                except OSError:
                    pass
            db.delete(up)
    _clear_scan_cache(sheet_id)


def _kunci(db):
    from server.db import Kunci
    return {k.name: k.data for k in db.scalars(select(Kunci))}


def _calib(db):
    from server.db import TemplateCalib
    return {c.field_name: (c.dx, c.dy) for c in db.scalars(select(TemplateCalib))}


def _norm_hp(raw: str):
    """Normalkan nomor HP Indonesia ke +62xxxxxxxxxx. Kembalikan None bila tidak valid.
    Terima: 0812…, 62812…, +62 812…, 812… (spasi/strip diabaikan). Nomor seluler: 8 + 8–11 digit."""
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("62"):
        d = d[2:]
    d = d.lstrip("0")
    return "+62" + d if re.fullmatch(r"8\d{8,11}", d) else None


def _pengawas(s: ScanSession):
    return {"nama": s.nama_pengawas, "hp": s.hp, "ruangan": s.ruangan, "kelas": s.kelas,
            "fakultas": s.fakultas, "prodi": s.prodi, "kode_soal": s.kode_soal, "hari_ujian": s.hari_ujian}


def _apply_identity(record: dict, s: ScanSession) -> dict:
    """Samakan kolom identitas pada hasil pindai dengan data sesi terkini (mis. setelah pengawas mengedit)."""
    updates = {"Nama Pengawas": s.nama_pengawas, "No HP Pengawas": s.hp, "Ruangan": s.ruangan or "-", "Program Studi": s.prodi}
    out = {}
    for k, v in record.items():
        if k == "Kelas":
            continue  # ditempatkan ulang tepat setelah Ruangan
        out[k] = updates.get(k, v)
        if k == "Ruangan" and s.kelas:
            out["Kelas"] = s.kelas
    if s.kelas:
        out.setdefault("Kelas", s.kelas)
    return out


# --------------------------------------------------------------------------- antrian
def _enqueue(file_id):
    with SessionLocal() as db:
        f = db.get(UploadFile, file_id)
        s = db.get(ScanSession, f.session_id)
        f.state = "processing"
        args = (f.path, f.name, _pengawas(s), _kunci(db))
        calib = _calib(db)
        db.commit()
    fut = _submit_scan(*args, calib=calib)
    _FUTURES[file_id] = fut
    fut.add_done_callback(lambda fu, fid=file_id: _on_done(fid, fu))


def _on_done(file_id, fut):
    _FUTURES.pop(file_id, None)
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
        sess = db.get(ScanSession, f.session_id)
        for r in results:
            seq += 1
            db.add(Sheet(session_id=f.session_id, file_id=f.id, page=r["page"], seq=seq,
                         doc_name=r["doc_name"], scan_status=r["status"], record=_apply_identity(r["record"], sess)))
        f.state = "failed" if err else "done"
        f.error = err
        db.commit()


def _enqueue_rescan(sheet_id):
    """Pindai ulang SATU lembar yg SUDAH punya hasil (bukan orphan -- bandingkan dgn _enqueue, yg utk berkas
    BARU tanpa Sheet sama sekali & membuat baris Sheet baru). Dipakai pindai-ulang massal per sesi (lihat
    admin.admin_rescan_all), mis. setelah perbaikan pipeline (deteksi pojok/kontras) supaya hasil lembar lama
    ikut disegarkan tanpa pengawas foto ulang. UPDATE baris Sheet yg sudah ada di tempat -- tak pernah
    menggandakan lembar. Kembalikan True bila berhasil diantre (False bila foto sumbernya sudah tak ada)."""
    with SessionLocal() as db:
        sh = db.get(Sheet, sheet_id)
        if sh is None:
            return False
        s = db.get(ScanSession, sh.session_id)
        up = db.get(UploadFile, sh.file_id)
        if not up or not up.path or not os.path.exists(up.path):
            return False
        args = (up.path, up.name, _pengawas(s), _kunci(db), sh.page)
        calib = _calib(db)
        session_id = sh.session_id
    with _rescan_lock:
        _RESCAN_PENDING[session_id] = _RESCAN_PENDING.get(session_id, 0) + 1
    fut = _submit_scan(*args, calib=calib)
    _RESCAN_FUTURES[sheet_id] = fut
    fut.add_done_callback(lambda fu, sid=sheet_id, ssid=session_id: _on_rescan_done(sid, ssid, fu))
    return True


def _on_rescan_done(sheet_id, session_id, fut):
    # Hitungan `_RESCAN_PENDING` dipakai UI polling (field `rescanning`) sbg tanda "sudah selesai, muat
    # ulang" -- jadi WAJIB diturunkan PALING TERAKHIR (finally), setelah baris Sheet benar2 ter-commit,
    # supaya polling yg lihat rescanning==0 tak pernah dapat data lembar ini yg masih basi.
    try:
        _RESCAN_FUTURES.pop(sheet_id, None)
        try:
            results = fut.result()
        except Exception:  # noqa: BLE001 -- satu lembar gagal tak boleh menghentikan sisanya; hasil lama dibiarkan apa adanya
            return
        if not results:
            return
        with SessionLocal() as db:
            sh = db.get(Sheet, sheet_id)
            if sh is None:  # lembar/sesi terhapus selagi diproses
                return
            s = db.get(ScanSession, sh.session_id)
            r = results[0]
            sh.doc_name, sh.scan_status, sh.record, sh.validated = r["doc_name"], r["status"], _apply_identity(r["record"], s), False
            db.commit()
        _clear_scan_cache(sheet_id)
    finally:
        with _rescan_lock:
            n = _RESCAN_PENDING.get(session_id, 1) - 1
            if n <= 0:
                _RESCAN_PENDING.pop(session_id, None)
            else:
                _RESCAN_PENDING[session_id] = n


def cancel_pending_files(db, s: ScanSession) -> dict:
    """Upaya admin utk MENGHENTIKAN pemindaian sesi `s` yg masih berjalan. DB menandai berkas 'processing'
    segera setelah diserahkan ke pool (lihat _enqueue) -- itu TIDAK berarti sedang benar2 dieksekusi oleh
    worker saat ini, krn ProcessPoolExecutor punya antrean internal sendiri saat semua worker sibuk. Jadi
    setiap berkas 'queued'/'processing' yg futurenya masih hidup dicoba dibatalkan lewat Future.cancel():
    berhasil (True) hanya bila belum benar2 diambil worker; kalau sudah benar2 jalan, cancel() gagal (False)
    dan tak ada cara aman menghentikannya di tengah jalan tanpa mematikan proses worker (bisa mengganggu sesi
    LAIN yg berbagi pool yg sama) -- dibiarkan selesai secara alami, hasilnya tetap masuk normal lewat
    _on_done (aman meski sesi ini kelak dihapus, lihat guard `f is None` di atas).
    Kembalikan {"dibatalkan": n, "masih_berjalan": n}."""
    cancelled = still_running = 0
    for f in s.files:
        if f.state not in ("queued", "processing"):
            continue
        fut = _FUTURES.get(f.id)
        if fut is not None and fut.cancel():
            _FUTURES.pop(f.id, None)
            f.state = "failed"
            f.error = "Dibatalkan oleh admin"
            cancelled += 1
        else:
            still_running += 1
    db.commit()
    return {"dibatalkan": cancelled, "masih_berjalan": still_running}


async def _loop(fn, every, first_delay=0):
    """Jalankan fn (blocking) berkala di thread terpisah; kegagalan dicatat, loop tidak berhenti."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            await asyncio.to_thread(fn)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logging.getLogger("uvicorn.error").exception("tugas latar %s gagal", getattr(fn, "__name__", fn))
        await asyncio.sleep(every)


@asynccontextmanager
async def lifespan(app):
    global _pool
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    init_db()
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, "1")   # diwarisi proses pemindai: cegah thread numpy/BLAS berebut core
    _pool = ProcessPoolExecutor(max_workers=config.SCAN_WORKERS, initializer=worker.init_worker, initargs=(os.getpid(), _pending))
    list(_pool.map(worker.warmup, range(config.SCAN_WORKERS)))  # muat template sekali per proses
    with SessionLocal() as db:  # pulihkan pekerjaan yang terputus saat restart
        pending = [f.id for f in db.scalars(select(UploadFile).where(UploadFile.state.in_(("queued", "processing"))))]
    for fid in pending:
        _enqueue(fid)
    tasks = []
    if os.environ.get("BACKGROUND_TASKS", "1") == "1":
        tasks = [
            asyncio.create_task(_loop(services.sync_pending_once, config.SYNC_INTERVAL_S, 5)),
            asyncio.create_task(_loop(services.sync_kunci_once, config.KUNCI_SYNC_INTERVAL_S, 3)),
            asyncio.create_task(_loop(services.cleanup_once, config.CLEANUP_INTERVAL_S, 30)),
        ]
    yield
    for t in tasks:
        t.cancel()
    _pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="LJK Scanner API", lifespan=lifespan,
    docs_url="/docs" if config.EXPOSE_DOCS else None, redoc_url=None,
    openapi_url="/openapi.json" if config.EXPOSE_DOCS else None)
app.add_middleware(
    SessionMiddleware, secret_key=config.SESSION_SECRET or secrets.token_urlsafe(32), session_cookie="ljk_admin",
    max_age=config.ADMIN_SESSION_HOURS * 3600, same_site="lax", https_only=config.PUBLIC_URL.startswith("https://"))


@app.middleware("http")
async def _revalidate_static(request, call_next):
    resp = await call_next(request)
    if request.method == "GET" and not request.url.path.startswith(("/api/", "/auth/")) and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-cache"   # selalu validasi ETag: perubahan UI langsung terlihat
    return resp


app.include_router(auth.router)
app.include_router(admin.router)


def get_db():
    with SessionLocal() as db:
        yield db


# --------------------------------------------------------------------------- meta & sesi
def _fakultas_options():
    """Daftar kode fakultas (mis. FIF, FTE, ...) langsung dari template LJK, bukan disalin manual."""
    field = load_default_template().get("fields", {}).get("FAKULTAS", {})
    items = field.get("items") or [{}]
    return [str(b.get("option")) for b in items[0].get("bubbles", []) if b.get("option")]


# Hari ujian literasi numerik: tanggal TETAP (bukan hari-dalam-minggu berulang), 28 Sep - 2 Okt 2026 --
# disimpan sbg tanggal ISO (mis. "2026-09-28"), label lengkap dgn nama hari utk ditampilkan.
HARI_UJIAN = [
    {"value": "2026-09-28", "label": "Senin, 28 September 2026"},
    {"value": "2026-09-29", "label": "Selasa, 29 September 2026"},
    {"value": "2026-09-30", "label": "Rabu, 30 September 2026"},
    {"value": "2026-10-01", "label": "Kamis, 1 Oktober 2026"},
    {"value": "2026-10-02", "label": "Jumat, 2 Oktober 2026"},
]
HARI_UJIAN_VALUES = [d["value"] for d in HARI_UJIAN]


def _kunci_names(db):
    return [k.name for k in db.scalars(select(Kunci).order_by(Kunci.name))]


@app.get("/api/meta")
def meta(db=Depends(get_db)):
    return {"pengawas": [{"id": p["id"], "nama": p["nama"], "dosen": p["dosen"], "needs_hp": not p["hp"] and not p["dosen"]} for p in refdata.pengawas()],
            "kelas": refdata.kelas(), "prodi": refdata.prodi_list(), "fakultas": _fakultas_options(),
            "kunci": _kunci_names(db), "hari": HARI_UJIAN,
            "max_upload_mb": config.MAX_UPLOAD_MB, "extensions": sorted(config.ALLOWED_EXT)}


@app.get("/api/kelas-check")
def kelas_check(fakultas: str, prodi: str, kelas: str, exclude_sid: str = "", db=Depends(get_db)):
    """Cek apakah kombinasi Fakultas/Prodi/Kelas ini SUDAH pernah dipakai sesi lain -- foto sumber kini
    disimpan per kelas (lihat _class_folder), dibagi antar sesi dgn fakultas/prodi/kelas yg sama. Dipanggil
    FE SEBELUM mulai unggah supaya bisa tanya konfirmasi ke pengawas ("kelas ini sudah ada isinya,
    lanjutkan?") -- tidak menghapus/menimpa apa pun di server, murni informasi."""
    cond = [ScanSession.fakultas == fakultas.strip(), ScanSession.prodi == prodi.strip(), ScanSession.kelas == kelas.strip()]
    if exclude_sid:
        cond.append(ScanSession.id != exclude_sid)
    existing = [s for s in db.scalars(select(ScanSession).where(*cond)) if s.files]
    if not existing:
        return {"exists": False}
    latest = max(existing, key=lambda s: s.created_at)
    return {"exists": True, "sesi": len(existing), "lembar": sum(len(s.sheets) for s in existing),
            "pengawas_terakhir": latest.nama_pengawas,
            "dibuat_terakhir": latest.created_at.isoformat() if latest.created_at else None}


class SessionIn(BaseModel):
    pengawas_ref: str = ""      # id dari /api/meta; kosong = isi sendiri (nama_pengawas + hp wajib)
    nama_pengawas: str = ""
    hp: str = ""
    kelas: str
    prodi: str = ""            # opsional bila kelas ada di tabel kelas (diisi server)
    fakultas: str = ""
    kode_soal: str = ""
    hari_ujian: str = ""
    replace_existing: bool = False   # hanya dipakai saat BUAT sesi: ganti sesi lama kelas yg sama (lihat create_session)


def _sync_pengawas_contact(ref_id: str, nama: str, hp: str):
    """Simpan nama+hp pengawas balik ke tabel `pengawas` (server/db.py Pengawas) setelah sesi dibuat/diubah
    -- DUA kasus: (1) pengawas TERDAFTAR yg blm py HP di tabel & baru mengisinya sendiri (needs_hp di FE)
    -- simpan HP itu spy lain kali tak perlu isi ulang; (2) pengawas pilih "Lainnya -- isi sendiri"
    (ref kosong) -- daftarkan sbg baris baru spy muncul di dropdown lain kali, tanpa admin perlu psql
    manual. Dicocokkan by nama PERSIS utk hindari dobel kalau orang yg sama isi manual berkali-kali.
    Gagal diam2 (noqa BLE001) -- ini kenyamanan sampingan, bukan bagian kritis alur submit sesi."""
    from server.db import Pengawas, SessionLocal
    if not nama:
        return
    try:
        with SessionLocal() as db2:
            if ref_id:
                p = db2.get(Pengawas, ref_id)
                if p and hp and not p.hp:
                    p.hp = hp
                    db2.commit()
            elif not db2.query(Pengawas).filter(Pengawas.nama == nama).first():
                db2.add(Pengawas(nama=nama, nim="", hp=hp or ""))
                db2.commit()
    except Exception:  # noqa: BLE001
        pass


def _resolve_identity(body: SessionIn, db):
    """Validasi isian dan kembalikan (nama, hp) pengawas: dari daftar terdaftar bila dipilih, atau isian sendiri."""
    errors = []
    info = refdata.kelas_info(body.kelas)
    if info:   # kelas ada di tabel -> prodi & fakultas SELALU dari tabel (isian klien diabaikan)
        body.prodi = info["prodi"]
        if info["fakultas"]:
            body.fakultas = info["fakultas"]
    nama, hp = body.nama_pengawas.strip(), _norm_hp(body.hp)
    dosen = False
    if body.pengawas_ref:
        p = refdata.pengawas_by_id(body.pengawas_ref)
        if p is None:
            errors.append("Pengawas tidak dikenal")
        else:
            nama = p["nama"]
            dosen = p["dosen"]
            hp = p["hp"] if dosen else (p["hp"] or hp)   # dosen: nomor HP tidak diminta (boleh kosong)
    if not nama:
        errors.append("Nama pengawas wajib diisi")
    if not hp and not dosen:
        errors.append("Nomor HP tidak valid (contoh: +62 812 3456 7890)")
    if not body.prodi.strip():
        errors.append("Program studi wajib diisi")
    elif len(body.prodi.strip()) > 150:
        errors.append("Program studi terlalu panjang")
    if not body.kelas.strip():
        errors.append("Kelas wajib diisi")
    elif len(body.kelas.strip()) > 100:
        errors.append("Kelas terlalu panjang")
    fakultas_opts = _fakultas_options()
    if body.fakultas.strip() not in fakultas_opts:
        errors.append(f"Fakultas wajib dipilih (salah satu dari: {', '.join(fakultas_opts)})")
    hari_ujian = body.hari_ujian.strip()
    if hari_ujian not in HARI_UJIAN_VALUES:
        errors.append("Hari ujian wajib dipilih")
    # Kode soal: isian singkat bebas (label bantu admin, mis. "273") -- TIDAK dicocokkan ke nama kunci
    # jawaban terdaftar; kunci yg dipakai saat menilai tetap ditentukan otomatis dari Kode Soal hasil
    # scan LJK per-mahasiswa (lihat core.evaluator.find_matching_kunci_sheet), bukan dari isian ini.
    kode_soal = body.kode_soal.strip()   # opsional: pengawas tak perlu mengisi lagi (kunci dipilih dari hasil scan)
    if len(kode_soal) > 20:
        errors.append("Kode soal terlalu panjang (maks. 20 karakter)")
    if errors:
        raise HTTPException(422, errors)
    _sync_pengawas_contact(body.pengawas_ref, nama, hp)
    return nama, hp, body.fakultas.strip(), kode_soal, hari_ujian


@app.post("/api/sessions", status_code=201)
def create_session(body: SessionIn, db=Depends(get_db)):
    nama, hp, fakultas, kode_soal, hari_ujian = _resolve_identity(body, db)
    replaced = 0
    if body.replace_existing:
        # Pengawas sudah mengonfirmasi "Upload ulang akan menimpa sesi upload sebelumnya": hapus sesi LAMA
        # fakultas/prodi/kelas yg sama (beserta lembar & fotonya). Endpoint ini publik, jadi sesi yg SUDAH
        # divalidasi admin / sudah terkirim ke Sheet SENGAJA tak disentuh -- hanya admin yg boleh menghapusnya.
        old = db.scalars(select(ScanSession).where(ScanSession.fakultas == fakultas, ScanSession.prodi == body.prodi.strip(),
                                                   ScanSession.kelas == body.kelas.strip())).all()
        for o in old:
            if o.admin_validated or o.submitted:
                continue
            cancel_pending_files(db, o)
            services.remove_session_files(o)
            db.delete(o)
            replaced += 1
        if replaced:
            db.commit()
    s = ScanSession(nama_pengawas=nama, hp=hp, ruangan="", kelas=body.kelas.strip(), fakultas=fakultas, prodi=body.prodi.strip(),
                     kode_soal=kode_soal, hari_ujian=hari_ujian)
    db.add(s)
    db.commit()
    return {"id": s.id, "diganti": replaced}


@app.patch("/api/sessions/{sid}")
def update_session(sid: str, body: SessionIn, db=Depends(get_db)):
    """Ubah identitas pengawas/kelas; seluruh lembar pada sesi ikut diperbarui."""
    s = _session_or_404(db, sid)
    if s.submitted:
        raise HTTPException(409, "Sesi sudah disubmit")
    nama, hp, fakultas, kode_soal, hari_ujian = _resolve_identity(body, db)
    s.nama_pengawas, s.hp = nama, hp
    s.kelas, s.prodi, s.fakultas = body.kelas.strip(), body.prodi.strip(), fakultas
    s.kode_soal, s.hari_ujian = kode_soal, hari_ujian
    for sh in s.sheets:
        sh.record = _apply_identity(sh.record, s)
    db.commit()
    return {"ok": True, "pengawas": _pengawas(s)}


def _session_or_404(db, sid):
    s = db.get(ScanSession, sid)
    if s is None:
        raise HTTPException(404, "Sesi tidak ditemukan")
    return s


def _sheet_view(sh: Sheet):
    """Data lembar untuk dashboard pengawas. Nama mahasiswa SENGAJA tidak disertakan di sini — tetap tersimpan
    di sh.record (rekap/ekspor admin) tapi tidak dikirim ke sisi pengawas sama sekali."""
    r = sh.record
    return {
        "id": sh.id, "seq": sh.seq, "file": r.get("File"),
        "npm": r.get("NPM"), "kode_soal": r.get("Kode Soal"),
        "fakultas_ljk": r.get("Fakultas (LJK)"), "fakultas": r.get("Fakultas"), "terisi": r.get("Jawaban Terisi"),
        "label": classify_scan_status({"status": sh.scan_status}, sh.validated),
        "validated": sh.validated,
    }


@app.get("/api/sessions/{sid}")
def get_session(sid: str, db=Depends(get_db)):
    s = _session_or_404(db, sid)
    files = s.files
    pending = sum(1 for f in files if f.state in ("queued", "processing"))
    sheets = [_sheet_view(x) for x in s.sheets]
    # Antrean per-berkas (queued/processing): pemindaian sebagian foto (mis. registrasi lembar sulit) bisa
    # sampai beberapa menit, jadi pengawas perlu tahu berkas MANA yang masih diproses, bukan cuma jumlahnya.
    queue = [{"name": f.name, "state": f.state} for f in files if f.state in ("queued", "processing")]
    return {
        "id": s.id, "pengawas": _pengawas(s), "submitted": s.submitted,
        "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        "files": {"total": len(files), "pending": pending, "queue": queue, "failed": [
            {"name": f.name, "error": f.error} for f in files if f.state == "failed"]},
        "scanning": pending > 0,
        "sheets": sheets,
        "summary": {"lembar": len(sheets), "ok": sum(1 for x in sheets if x["label"] == "OK"),
                    "perlu_validasi": sum(1 for x in sheets if x["label"] == "Perlu Validasi"),
                    "gagal": sum(1 for x in sheets if x["label"] == "Gagal")},
    }


@app.get("/api/monitor-sesi")
def monitor_sesi(db=Depends(get_db)):
    """Status pemindaian SEMUA sesi, PUBLIK (tanpa token admin) -- supaya pengawas & siapa pun yg tahu URL-nya
    bisa memantau progres scan tanpa perlu login. Sengaja TIDAK menyertakan nomor HP pengawas (data pribadi)
    atau apa pun soal mahasiswa (NPM/nama/jawaban) -- hanya status proses per sesi, sama spt yg admin lihat
    di tab Sesi tapi tanpa detail sensitif & tanpa aksi kelola."""
    rows = db.scalars(select(ScanSession).order_by(ScanSession.created_at.desc()))
    items = []
    by_kelas_lembar = {}
    try:
        jml_mhs_map = admin._jml_mhs_by_kelas()   # jumlah mahasiswa per kelas dari jadwal plotting (data jadwal, bukan data peserta)
    except Exception:  # noqa: BLE001
        jml_mhs_map = {}
    for s in rows:
        n_pending = sum(1 for f in s.files if f.state in ("queued", "processing"))
        status = "scanning" if n_pending else ("validated" if s.admin_validated else "perlu_cek")
        n_files = len(s.files)   # jumlah FOTO terupload, bukan jumlah lembar hasil scan -- lihat admin.monitor()
        items.append({
            "nama": s.nama_pengawas, "kelas": s.kelas, "prodi": s.prodi,
            "kode_soal": s.kode_soal, "hari_ujian": s.hari_ujian,
            "lembar": n_files, "status": status,
            "jml_mhs": jml_mhs_map.get((s.kelas or "").strip().lower()),
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
        key = (s.kelas or "").strip().lower()
        by_kelas_lembar[key] = by_kelas_lembar.get(key, 0) + n_files
    # Persentase upload BERBASIS JUMLAH KELAS (bukan lembar/mahasiswa) -- seragam dgn tab Monitoring admin:
    # dari kelas onsite di jadwal plotting, berapa yg SUDAH ada unggahan sama sekali (lepas status validasi).
    try:
        rows_sched, _fetched, _perr = plotting.schedule()
    except Exception:  # noqa: BLE001
        rows_sched = []
    onsite_kelas = {(r["kelas"] or "").strip().lower() for r in rows_sched
                     if (r.get("mode") or "").lower() == "onsite" and r.get("kelas")}
    kelas_total = len(onsite_kelas)
    kelas_upload = sum(1 for k in onsite_kelas if by_kelas_lembar.get(k, 0) > 0)
    return {"items": items, "kelas_upload": kelas_upload, "kelas_total": kelas_total,
            "upload_pct": round(kelas_upload / kelas_total * 100, 1) if kelas_total else 0}


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
    have = db.scalar(select(func.count()).select_from(UploadFile).where(UploadFile.session_id == s.id)) or 0
    if have + len(files) > config.MAX_FILES_PER_SESSION:
        raise HTTPException(413, f"Batas {config.MAX_FILES_PER_SESSION} berkas per sesi terlampaui")
    folder = _class_folder(s)
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


class SheetCorrectionIn(BaseModel):
    npm: str | None = None
    kode_soal: str | None = None
    fakultas_ljk: str | None = None


@app.patch("/api/sheets/{shid}")
def correct_sheet(shid: str, body: SheetCorrectionIn, db=Depends(get_db)):
    """Koreksi manual NPM/kode soal/fakultas dari dashboard pengawas saat validasi (silang di LJK sulit terbaca:
    fotokopi, tinta samar, dsb). Hanya field yang dikirim (bukan None) yang diubah; nilai diselaraskan ulang
    memakai kunci saat ini bila kode soal berubah."""
    sh = _sheet_or_404(db, shid)
    _guard_open(db, sh)
    rec = dict(sh.record)
    if body.npm is not None:
        npm = re.sub(r"\D", "", body.npm)
        if npm and len(npm) != 10:
            raise HTTPException(422, "NPM harus 10 digit")
        rec["NPM"] = npm
    if body.kode_soal is not None:
        kode = body.kode_soal.strip()
        if kode and not re.fullmatch(r"\d{3}", kode):
            raise HTTPException(422, "Kode soal harus 3 digit")
        rec["Kode Soal"] = kode
    if body.fakultas_ljk is not None:
        fak = body.fakultas_ljk.strip().upper()
        options = _fakultas_options()
        if fak and fak not in options:
            raise HTTPException(422, f"Fakultas harus salah satu dari: {', '.join(options)}")
        rec["Fakultas (LJK)"] = fak
        rec["Fakultas"] = fak
    from core.evaluator import grade_student_record
    k = services.kunci_int(db)
    sh.record = grade_student_record(rec, k) if k else rec
    db.commit()
    return _sheet_view(sh)


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


class BatchIn(BaseModel):
    ids: list[str]
    value: bool = True


@app.post("/api/sheets/validate-batch")
def validate_batch(body: BatchIn, db=Depends(get_db)):
    """Validasi/batalkan validasi sekumpulan lembar (mis. halaman yang sedang ditampilkan)."""
    sheets = list(db.scalars(select(Sheet).where(Sheet.id.in_(body.ids))))
    if not sheets:
        return {"changed": 0}
    if len({x.session_id for x in sheets}) != 1:
        raise HTTPException(422, "Lembar harus berasal dari satu sesi")
    if db.get(ScanSession, sheets[0].session_id).submitted:
        raise HTTPException(409, "Sesi sudah disubmit")
    changed = 0
    for sh in sheets:
        if body.value and classify_scan_status({"status": sh.scan_status}, False) == "Gagal":
            continue  # lembar gagal tidak bisa divalidasi
        if sh.validated != body.value:
            sh.validated = body.value
            changed += 1
    db.commit()
    return {"changed": changed}


@app.delete("/api/sheets/{shid}", status_code=204)
def delete_sheet(shid: str, db=Depends(get_db)):
    sh = _sheet_or_404(db, shid)
    _guard_open(db, sh)
    _delete_sheet_and_cleanup(db, sh)
    db.commit()


@app.post("/api/sheets/{shid}/replace")
async def replace_photo(shid: str, file: FUploadFile = File(...), db=Depends(get_db)):
    sh = _sheet_or_404(db, shid)
    _guard_open(db, sh)
    s = db.get(ScanSession, sh.session_id)
    _check_ext(file.filename or "")
    dest = os.path.join(_class_folder(s), f"{uuid.uuid4().hex[:8]}_{_safe_name(file.filename)}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    size = await _save_stream(file, dest)
    fut = _submit_scan(dest, file.filename, _pengawas(s), _kunci(db), 0, calib=_calib(db))
    try:
        res = fut.result(timeout=120)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"Gagal memproses foto baru: {e}")
    if not res:
        raise HTTPException(422, "Foto baru tidak berisi halaman")
    up = db.get(UploadFile, sh.file_id)
    up.path, up.name, up.size = dest, file.filename, size
    sh.page, sh.doc_name, sh.scan_status, sh.record, sh.validated = 0, res[0]["doc_name"], res[0]["status"], _apply_identity(res[0]["record"], s), False
    db.commit()
    _clear_scan_cache(shid)
    return _sheet_view(sh)


@app.get("/api/sheets/{shid}/preview")
def preview(shid: str, db=Depends(get_db)):
    """'Hasil scan': gambar hasil preprocessing + bubble terbaca. Dicache di disk (lihat _scan_cache_path)
    krn pipeline penuh dgn overlay bisa makan beberapa detik -- klik ulang biasa cukup disajikan dari cache.
    Cache diperbarui (dihapus lalu dibuat ulang di sini) tiap kali lembar ini dipindai ulang/foto diganti
    (_clear_scan_cache di rescan/replace), & dihapus permanen begitu sesi 'Sent' (lihat services.py) krn
    admin sudah tak perlu melihatnya lagi."""
    sh = _sheet_or_404(db, shid)
    cache_path = _scan_cache_path(shid)
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return Response(f.read(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    s = db.get(ScanSession, sh.session_id)
    up = db.get(UploadFile, sh.file_id)
    if not os.path.exists(up.path):
        raise HTTPException(410, "Berkas sumber sudah dihapus")
    fut = _submit_scan(up.path, up.name, _pengawas(s), _kunci(db), sh.page, True, calib=_calib(db))
    res = fut.result(timeout=120)
    if not res or "overlay_jpeg" not in res[0]:
        raise HTTPException(404, "Preview tidak tersedia")
    jpeg = res[0]["overlay_jpeg"]
    _save_scan_cache(shid, jpeg)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
    services.regrade_session(db, s)   # kunci bisa diunggah setelah pemindaian
    s.submitted, s.submitted_at = True, datetime.now(timezone.utc)
    s.synced_at, s.sync_attempts, s.sync_error, s.sync_next = None, 0, None, None
    db.commit()
    return {"ok": True, "lembar": len(s.sheets)}


_CLOG = {}


@app.post("/api/clientlog", status_code=204)
def clientlog(data: dict, request: Request):
    """Log diagnostik dari browser (mis. kegagalan upload di ponsel) agar terlihat di terminal server.
    Publik & tanpa login, jadi dibatasi lajunya per IP supaya tidak bisa dipakai memenuhi log."""
    ip = request.client.host if request.client else "?"
    now = time.time()
    recent = [t for t in _CLOG.get(ip, []) if now - t < 60]
    if len(recent) >= config.CLIENTLOG_PER_MIN:
        _CLOG[ip] = recent
        return
    recent.append(now)
    _CLOG[ip] = recent
    if len(_CLOG) > 5000:
        _CLOG.clear()
    logging.getLogger("uvicorn.error").info("CLIENT %s", str(data)[:600])


@app.get("/api/health")
def health():
    return {"ok": True, "workers": config.SCAN_WORKERS}


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "admin.html"))


# UI statis (tanpa build). Dipasang terakhir agar tidak menimpa rute /api.
@app.get("/ljk", include_in_schema=False)
def ljk_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "ljk.html"))


@app.get("/panduan", include_in_schema=False)
def panduan_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "panduan.html"))


@app.get("/monitor", include_in_schema=False)
def monitor_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "monitor.html"))


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="ui")
