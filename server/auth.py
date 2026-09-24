"""Login admin dengan akun Microsoft (OIDC authorization code + PKCE lewat MSAL). Sesi = cookie bertanda tangan."""
import hashlib
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
    """Login Microsoft terkonfigurasi."""
    return bool(config.MS_CLIENT_ID and config.MS_TENANT_ID and config.MS_CLIENT_SECRET)


def google_enabled() -> bool:
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)


def sso_enabled() -> bool:
    """Ada penyedia login (Microsoft/Google) aktif -> ADMIN_TOKEN dinonaktifkan kecuali ADMIN_ALLOW_TOKEN=1."""
    return enabled() or google_enabled()


def password_enabled() -> bool:
    return bool(config.ADMIN_USER and config.ADMIN_PASSWORD_HASH)


def hash_password(pw: str, iters: int = 240000) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iters)
    return f"{iters}:{salt.hex()}:{dk.hex()}"


def _check_password(pw: str, stored: str) -> bool:
    try:
        iters, salt, dk = stored.split(":")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(got, bytes.fromhex(dk))
    except Exception:  # noqa: BLE001
        return False


_FAILS = {}            # ip -> [waktu gagal]


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
    if token_ok and (not sso_enabled() or config.ADMIN_ALLOW_TOKEN):
        return {"email": "token", "name": "ADMIN_TOKEN"}
    raise HTTPException(401, "Belum masuk sebagai admin")


@router.get("/auth/me")
def me(request: Request):
    s = current_admin(request)
    return {"microsoft": enabled(), "google": google_enabled(), "authed": bool(s),
            "email": (s or {}).get("email"), "name": (s or {}).get("name"), "via": (s or {}).get("via"),
            "password": password_enabled(),
            "token_allowed": (not sso_enabled()) or config.ADMIN_ALLOW_TOKEN}


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
    request.session["admin"] = {"email": email, "name": claims.get("name", ""), "iat": int(time.time()), "via": "microsoft"}
    return RedirectResponse("/admin", status_code=302)


@router.post("/auth/password")
async def password_login(request: Request):
    if not password_enabled():
        raise HTTPException(404, "Login password belum dikonfigurasi")
    if not _same_origin(request):
        raise HTTPException(403, "Permintaan lintas-situs ditolak")
    ip = request.client.host if request.client else "?"
    now = time.time()
    with _LOCK:
        fails = [t for t in _FAILS.get(ip, []) if now - t < 900]
        _FAILS[ip] = fails
        if len(fails) >= 5:
            raise HTTPException(429, "Terlalu banyak percobaan. Coba lagi 15 menit lagi.")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    user, pw = str(body.get("username", "")), str(body.get("password", ""))
    ok_user = hmac.compare_digest(user.encode(), config.ADMIN_USER.encode())
    ok_pw = _check_password(pw, config.ADMIN_PASSWORD_HASH)
    if not (ok_user and ok_pw):
        with _LOCK:
            _FAILS.setdefault(ip, []).append(now)
        raise HTTPException(401, "Username atau password salah")
    with _LOCK:
        _FAILS.pop(ip, None)
    request.session["admin"] = {"email": config.ADMIN_USER, "name": config.ADMIN_USER, "iat": int(now), "via": "password"}
    return {"ok": True}


@router.post("/auth/logout")
def logout(request: Request):
    if "session" in request.scope:
        request.session.clear()
    return JSONResponse({"ok": True})


# ============================================================================= Google
_GFLOWS = {}           # state -> (nonce, code_verifier, waktu)
_g_override = None


def set_google(g):
    """Untuk pengujian: ganti pertukaran kode Google dengan tiruan (objek dengan .exchange(code, verifier, redirect_uri))."""
    global _g_override
    _g_override = g


class _GoogleOIDC:
    """Tukar kode -> ID token, lalu verifikasi tanda tangan, audience, issuer, kedaluwarsa (google-auth)."""

    def exchange(self, code, verifier, redirect_uri):
        import httpx
        from google.auth.transport import requests as grequests
        from google.oauth2 import id_token
        r = httpx.post("https://oauth2.googleapis.com/token", timeout=15, data={
            "code": code, "client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier})
        r.raise_for_status()
        return id_token.verify_oauth2_token(r.json()["id_token"], grequests.Request(), config.GOOGLE_CLIENT_ID)


def _google_redirect_uri(request: Request) -> str:
    return (config.PUBLIC_URL or str(request.base_url).rstrip("/")) + "/auth/google/callback"


def is_allowed_google(claims: dict) -> bool:
    """Default-deny. Email harus terverifikasi & ada di ADMIN_EMAILS, atau domain Workspace (klaim `hd`) ada di ADMIN_DOMAINS.
    Domain email biasa (mis. gmail.com) TIDAK pernah cukup — kalau tidak, semua pengguna Gmail bisa jadi admin."""
    if not claims.get("email_verified"):
        return False
    email = (claims.get("email") or "").strip().lower()
    if email and email in config.ADMIN_EMAILS:
        return True
    return bool(claims.get("hd")) and claims["hd"].lower() in config.ADMIN_DOMAINS


@router.get("/auth/google/login")
def google_login(request: Request):
    import base64
    import hashlib
    import secrets as _sec
    from urllib.parse import urlencode
    if not google_enabled():
        raise HTTPException(404, "Login Google belum dikonfigurasi")
    state, nonce, verifier = _sec.token_urlsafe(24), _sec.token_urlsafe(16), _sec.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with _LOCK:
        now = time.time()
        for k in [k for k, v in _GFLOWS.items() if now - v[2] > 600]:
            _GFLOWS.pop(k, None)
        _GFLOWS[state] = (nonce, verifier, now)
    q = urlencode({"client_id": config.GOOGLE_CLIENT_ID, "redirect_uri": _google_redirect_uri(request), "response_type": "code",
                   "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge": challenge,
                   "code_challenge_method": "S256", "prompt": "select_account"})
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + q, status_code=302)


@router.get("/auth/google/callback")
def google_callback(request: Request):
    if not google_enabled():
        raise HTTPException(404, "Login Google belum dikonfigurasi")
    params = dict(request.query_params)
    if params.get("error"):
        return _back("dibatalkan")
    with _LOCK:
        entry = _GFLOWS.pop(params.get("state", ""), None)
    if not entry or time.time() - entry[2] > 600 or not params.get("code"):
        return _back("kedaluwarsa")
    nonce, verifier, _ = entry
    try:
        claims = (_g_override or _GoogleOIDC()).exchange(params["code"], verifier, _google_redirect_uri(request))
    except Exception as e:  # noqa: BLE001
        logging.getLogger("uvicorn.error").error("Login Google gagal: %s", str(e)[:300])
        return _back("gagal")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        return _back("gagal")
    if not is_allowed_google(claims):
        return _back("ditolak")
    request.session["admin"] = {"email": claims["email"].strip().lower(), "name": claims.get("name", ""),
                                "iat": int(time.time()), "via": "google"}
    return RedirectResponse("/admin", status_code=302)


if __name__ == "__main__":
    import getpass
    import sys
    if sys.argv[1:] == ["hash"]:
        print(hash_password(getpass.getpass("Password: ")))
