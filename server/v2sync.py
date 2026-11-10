"""Endpoint internal untuk LJK Scanner v2 (Next.js, satu jaringan docker dengan API ini).

v2 tidak memegang kredensial Google: ia mengirim baris LJK tervalidasi ke sini, lalu API v1 yang
menulis ke spreadsheet yang SAMA (GSHEET_URL) di tab terpisah (GSHEET_V2_WORKSHEET). Baris dengan
`Kunci` (Kelas|NIM) yang sama diperbarui di tempat, jadi tombol kirim aman dipakai berulang.
Dilindungi token bersama V2_SYNC_TOKEN (header x-v2-token); tanpa token endpoint nonaktif.
"""
import hmac
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from server import sheets

router = APIRouter(prefix="/api/v2", include_in_schema=False)
WORKSHEET = os.environ.get("GSHEET_V2_WORKSHEET", "Profiling FIF (v2)")


class _KirimIn(BaseModel):
    kelas_id: str
    records: list[dict]


@router.post("/kirim")
def kirim(body: _KirimIn, x_v2_token: str = Header(default="")):
    token = os.environ.get("V2_SYNC_TOKEN", "")
    if not token or not hmac.compare_digest(x_v2_token, token):
        raise HTTPException(401, "token tidak valid")
    if len(body.records) > 2000:
        raise HTTPException(413, "terlalu banyak baris")
    client = sheets.get_client()
    if client is None:
        raise HTTPException(503, "Google Sheet belum dikonfigurasi di server (GSHEET_URL/GSHEET_CREDENTIALS)")
    recs = [{k: ("" if v is None else v) for k, v in r.items()} for r in body.records]
    try:
        # tab baru dibuat cukup lebar (identitas + 15 kuisioner + 75 soal + metadata > 100 kolom)
        sh = client._spreadsheet()
        try:
            sh.worksheet(WORKSHEET)
        except Exception:
            sh.add_worksheet(title=WORKSHEET, rows=2000, cols=max(120, max((len(r) for r in recs), default=0) + 5))
        res = client.upsert_records(recs, key_col="Kunci", ws_name=WORKSHEET)
    except sheets.SheetsUnavailable as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # noqa: BLE001 - teruskan pesan gspread ke admin
        raise HTTPException(502, f"Gagal menulis ke Google Sheet: {e}")
    return {"ok": True, "worksheet": WORKSHEET, **(res or {})}


# ------------------------------------------------------------------ kehadiran (sheet per kelas)
# Setiap LJK yang berhasil discan di v2 menandai mahasiswa "Hadir" di spreadsheet kehadiran Pendikar:
# satu tab per kelas (nama tab = kode kelas), kolom NIM & Kehadiran. Permintaan dikumpulkan lalu ditulis
# per tab secara berkala (batch) agar tidak terkena batas kuota Google Sheets saat scan beruntun.
import logging
import threading
import time

log = logging.getLogger("v2sync")
KEHADIRAN_URL = os.environ.get(
    "GSHEET_KEHADIRAN_URL", "https://docs.google.com/spreadsheets/d/1A_SxdgAJ7OyJDxuo-mqw8asudVmCnQnsODodWTw6IWI/edit"
)
HADIR = os.environ.get("KEHADIRAN_NILAI", "Hadir")
_pending: dict[str, set[str]] = {}
_plock = threading.Lock()
_last = {"ok_at": None, "error": None}
_worker_started = False


class _HadirIn(BaseModel):
    kelas_id: str
    nims: list[str]


def _flush_once():
    with _plock:
        batch = {k: v for k, v in _pending.items() if v}
        _pending.clear()
    if not batch:
        return
    client = sheets.get_client()
    if client is None:
        raise RuntimeError("kredensial Google Sheet belum dikonfigurasi")
    sh = client._client().open_by_url(KEHADIRAN_URL)
    for kelas, nims in batch.items():
        try:
            ws = sh.worksheet(kelas)
            rows = ws.get_all_values()
            if not rows:
                continue
            head = [h.strip().lower() for h in rows[0]]
            ci_nim, ci_had = head.index("nim"), head.index("kehadiran")
            col = chr(ord("A") + ci_had)
            upd = []
            for r, row in enumerate(rows[1:], start=2):
                nim = (row[ci_nim] if ci_nim < len(row) else "").strip()
                cur = (row[ci_had] if ci_had < len(row) else "").strip()
                if nim in nims and cur != HADIR:
                    upd.append({"range": f"{col}{r}", "values": [[HADIR]]})
            if upd:
                ws.batch_update(upd, value_input_option="USER_ENTERED")
            log.info("kehadiran %s: %d ditandai", kelas, len(upd))
        except Exception as e:  # noqa: BLE001
            log.warning("kehadiran %s gagal: %s", kelas, e)
            with _plock:  # coba lagi pada putaran berikutnya
                _pending.setdefault(kelas, set()).update(nims)
            raise


def _worker():
    while True:
        time.sleep(5)
        try:
            _flush_once()
            _last.update(ok_at=time.time(), error=None)
        except Exception as e:  # noqa: BLE001
            _last["error"] = str(e)
            time.sleep(30)


@router.post("/hadir")
def hadir(body: _HadirIn, x_v2_token: str = Header(default="")):
    global _worker_started
    token = os.environ.get("V2_SYNC_TOKEN", "")
    if not token or not hmac.compare_digest(x_v2_token, token):
        raise HTTPException(401, "token tidak valid")
    nims = {n for n in body.nims if n.isdigit() and len(n) == 12}
    with _plock:
        _pending.setdefault(body.kelas_id, set()).update(nims)
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True, name="kehadiran").start()
            _worker_started = True
    return {"ok": True, "antre": len(nims), "terakhir_error": _last["error"]}
