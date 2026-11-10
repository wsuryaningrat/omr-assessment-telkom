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
