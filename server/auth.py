"""Login admin dengan akun Microsoft (OIDC authorization code + PKCE lewat MSAL). Sesi = cookie bertanda tangan."""
import hmac
import logging
import os
import re
import threading
import time
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from server import config

router = APIRouter()
_FLOWS = {}            # state -> (flow, waktu) ; disimpan di server (bukan di cookie)
_LOCK = threading.Lock()
_override = None
SAFE = {"GET", "HEAD", "OPTIONS"}


def enabled() -> bool:
    return bool(config.MS_CLIENT_ID and config.MS_TENANT_ID and config.MS_CLIENT_SECRET)


def set_app(a):
    """Untuk pengujian: ganti klien MSAL dengan tiruan."""
    global _override
    _override = a


_cache = {}


def _app():
    """Aplikasi MSAL (di-cache: pembuatannya menghubungi Microsoft untuk discovery)."""
    if _override is not None:
        return _override
    key = (config.MS_CLIENT_ID, config.MS_TENANT_ID, config.MS_CLIENT_SECRET)
    if key not in _cache:
        import msal
        _cache.clear()
        _cache[key] = msal.ConfidentialClientApplication(
            config.MS_CLIENT_ID, client_credential=config.MS_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}")
    return _cache[key]


def _redirect_uri(request: Request) -> str:
    base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    return base + "/auth/callback"


def is_allowed(email: str) -> bool:
    """Default-deny: hanya email di ADMIN_EMAILS atau berdomain di ADMIN_DOMAINS."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False
    if email in config.ADMIN_EMAILS:
        return True
    return email.rsplit("@", 1)[1] in config.ADMIN_DOMAINS


def _purge():
    now = time.time()
    for k in [k for k, (_, t) in _FLOWS.items() if now - t > 600]:
        _FLOWS.pop(k, None)


def _fresh(sess) -> bool:
    return bool(sess) and time.time() - sess.get("iat", 0) < config.ADMIN_SESSION_HOURS * 3600


def _same_origin(request: Request) -> bool:
    """Perlindungan CSRF untuk permintaan yang mengubah data dengan cookie."""
    origin, host = request.headers.get("origin"), request.headers.get("host", "")
    if origin:
        return urlparse(origin).netloc == host
    return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")


def current_admin(request: Request):
    sess = request.session.get("admin") if "session" in request.scope else None
    return sess if _fresh(sess) else None


def require_admin(request: Request, x_admin_token: str = Header(default="")):
    """Dependency untuk semua endpoint admin: sesi Microsoft, atau (bila diizinkan) header ADMIN_TOKEN."""
    sess = current_admin(request)
    if sess:
        if request.method not in SAFE and not _same_origin(request):
            raise HTTPException(403, "Permintaan lintas-situs ditolak")
        return sess
    token = os.environ.get("ADMIN_TOKEN", "")
    token_ok = bool(token) and hmac.compare_digest(x_admin_token.encode(), token.encode())
    if token_ok and (not enabled() or config.ADMIN_ALLOW_TOKEN):
        return {"email": "token", "name": "ADMIN_TOKEN"}
    raise HTTPException(401, "Belum masuk sebagai admin")


@router.get("/auth/me")
def me(request: Request):
    s = current_admin(request)
    return {"microsoft": enabled(), "authed": bool(s), "email": (s or {}).get("email"), "name": (s or {}).get("name"),
            "token_allowed": (not enabled()) or config.ADMIN_ALLOW_TOKEN}


@router.get("/auth/login")
def login(request: Request):
    if not enabled():
        raise HTTPException(404, "Login Microsoft belum dikonfigurasi")
    try:
        flow = _app().initiate_auth_code_flow(scopes=["User.Read"], redirect_uri=_redirect_uri(request))
    except Exception as e:  # noqa: BLE001 — tenant/client salah atau Microsoft tidak terjangkau
        logging.getLogger("uvicorn.error").error("Login Microsoft gagal dimulai: %s", str(e)[:300])
        return _back("konfigurasi")
    with _LOCK:
        _purge()
        _FLOWS[flow["state"]] = (flow, time.time())
    return RedirectResponse(flow["auth_uri"], status_code=302)


def _back(code: str):
    return RedirectResponse(f"/admin?err={quote(code)}", status_code=302)


@router.get("/auth/callback")
def callback(request: Request):
    if not enabled():
        raise HTTPException(404, "Login Microsoft belum dikonfigurasi")
    params = dict(request.query_params)
    if params.get("error"):
        return _back("dibatalkan")
    with _LOCK:
        entry = _FLOWS.pop(params.get("state", ""), None)
    if not entry or time.time() - entry[1] > 600:
        return _back("kedaluwarsa")
    try:
        result = _app().acquire_token_by_auth_code_flow(entry[0], params)
    except Exception:  # noqa: BLE001
        return _back("gagal")
    if not isinstance(result, dict) or "error" in result:
        return _back("gagal")
    claims = result.get("id_token_claims") or {}
    if re.fullmatch(r"[0-9a-fA-F-]{36}", config.MS_TENANT_ID) and claims.get("tid", "").lower() != config.MS_TENANT_ID.lower():
        return _back("tenant")
    email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    if not is_allowed(email):
        return _back("ditolak")
    request.session["admin"] = {"email": email, "name": claims.get("name", ""), "iat": int(time.time())}
    return RedirectResponse("/admin", status_code=302)


@router.post("/auth/logout")
def logout(request: Request):
    if "session" in request.scope:
        request.session.clear()
    return JSONResponse({"ok": True})
