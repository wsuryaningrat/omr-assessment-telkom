"""Uji login admin Microsoft (MSAL dipalsukan — tanpa jaringan): alur, allowlist default-deny, tenant, CSRF, kedaluwarsa."""
import unittest
import uuid

from fastapi.testclient import TestClient

from server import auth, config
from server.main import app

TENANT = "11111111-2222-3333-4444-555555555555"
ADM = {"X-Admin-Token": "rahasia"}


class FakeMsal:
    def __init__(self):
        self.claims, self.error = {}, False

    def initiate_auth_code_flow(self, scopes, redirect_uri=None):
        st = "st-" + uuid.uuid4().hex[:8]
        return {"state": st, "redirect": redirect_uri, "auth_uri": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize?state={st}"}

    def acquire_token_by_auth_code_flow(self, flow, params):
        return {"error": "invalid_grant"} if self.error else {"id_token_claims": self.claims}


class TestMicrosoftLogin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = TestClient(app, follow_redirects=False)
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        self.saved = {k: getattr(config, k) for k in ("MS_CLIENT_ID", "MS_TENANT_ID", "MS_CLIENT_SECRET", "ADMIN_EMAILS",
                                                     "ADMIN_DOMAINS", "ADMIN_ALLOW_TOKEN", "ADMIN_SESSION_HOURS")}
        config.MS_CLIENT_ID, config.MS_TENANT_ID, config.MS_CLIENT_SECRET = "cid", TENANT, "sec"
        config.ADMIN_EMAILS, config.ADMIN_DOMAINS, config.ADMIN_ALLOW_TOKEN = ["boss@kampus.ac.id"], [], False
        self.fake = FakeMsal()
        self.fake.claims = {"tid": TENANT, "preferred_username": "Boss@Kampus.ac.id", "name": "Pak Boss"}
        auth.set_app(self.fake)
        self.c.cookies.clear()

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)
        auth.set_app(None)
        self.c.cookies.clear()

    def sign_in(self):
        r = self.c.get("/auth/login")
        self.assertEqual(r.status_code, 302)
        self.assertIn("login.microsoftonline.com", r.headers["location"])
        state = r.headers["location"].split("state=")[1]
        return self.c.get(f"/auth/callback?code=abc&state={state}"), state

    def test_disabled_when_not_configured(self):
        config.MS_CLIENT_ID = ""
        self.assertEqual(self.c.get("/auth/login").status_code, 404)
        m = self.c.get("/auth/me").json()
        self.assertEqual((m["microsoft"], m["token_allowed"], m["authed"]), (False, True, False))
        self.assertEqual(self.c.get("/api/admin/summary", headers=ADM).status_code, 200)   # token tetap jalan

    def test_token_disabled_once_microsoft_is_on(self):
        self.assertEqual(self.c.get("/api/admin/summary", headers=ADM).status_code, 401)
        self.assertFalse(self.c.get("/auth/me").json()["token_allowed"])
        config.ADMIN_ALLOW_TOKEN = True
        self.assertEqual(self.c.get("/api/admin/summary", headers=ADM).status_code, 200)

    def test_full_login_and_logout(self):
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 401)
        r, _ = self.sign_in()
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/admin"))
        me = self.c.get("/auth/me").json()
        self.assertEqual((me["authed"], me["email"], me["name"]), (True, "boss@kampus.ac.id", "Pak Boss"))   # email dinormalkan
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 200)                                   # tanpa token
        self.assertEqual(self.c.post("/auth/logout").status_code, 200)
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 401)

    def test_unlisted_account_is_denied(self):
        self.fake.claims["preferred_username"] = "orang.lain@kampus.ac.id"
        r, _ = self.sign_in()
        self.assertEqual(r.headers["location"], "/admin?err=ditolak")
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 401)

    def test_domain_allowlist(self):
        config.ADMIN_EMAILS, config.ADMIN_DOMAINS = [], ["kampus.ac.id"]
        self.fake.claims["preferred_username"] = "siapa.saja@kampus.ac.id"
        self.assertEqual(self.sign_in()[0].headers["location"], "/admin")
        self.c.cookies.clear()
        self.fake.claims["preferred_username"] = "x@kampus.ac.id.evil.com"      # domain menyerupai tidak boleh lolos
        self.assertEqual(self.sign_in()[0].headers["location"], "/admin?err=ditolak")

    def test_default_deny_without_allowlist(self):
        config.ADMIN_EMAILS, config.ADMIN_DOMAINS = [], []
        self.assertEqual(self.sign_in()[0].headers["location"], "/admin?err=ditolak")

    def test_wrong_tenant_is_rejected(self):
        self.fake.claims["tid"] = "99999999-9999-9999-9999-999999999999"
        self.assertEqual(self.sign_in()[0].headers["location"], "/admin?err=tenant")

    def test_state_is_single_use_and_unknown_state_rejected(self):
        r, state = self.sign_in()
        self.assertEqual(r.headers["location"], "/admin")
        self.c.cookies.clear()
        self.assertEqual(self.c.get(f"/auth/callback?code=abc&state={state}").headers["location"], "/admin?err=kedaluwarsa")   # replay
        self.assertEqual(self.c.get("/auth/callback?code=abc&state=palsu").headers["location"], "/admin?err=kedaluwarsa")

    def test_misconfigured_microsoft_shows_message_not_500(self):
        def boom(*a, **k):
            raise ValueError("OIDC Discovery failed")
        self.fake.initiate_auth_code_flow = boom
        r = self.c.get("/auth/login")
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/admin?err=konfigurasi"))

    def test_msal_error_and_user_cancel(self):
        self.fake.error = True
        self.assertEqual(self.sign_in()[0].headers["location"], "/admin?err=gagal")
        self.assertEqual(self.c.get("/auth/callback?error=access_denied").headers["location"], "/admin?err=dibatalkan")

    def test_csrf_blocks_cross_origin_writes_but_allows_same_origin(self):
        self.sign_in()
        evil = self.c.post("/api/admin/cleanup", headers={"Origin": "https://evil.example"})
        self.assertEqual(evil.status_code, 403)
        ok = self.c.post("/api/admin/cleanup", headers={"Origin": "http://testserver"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.c.get("/api/admin/summary", headers={"Origin": "https://evil.example"}).status_code, 200)   # GET aman

    def test_session_expires(self):
        self.sign_in()
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 200)
        config.ADMIN_SESSION_HOURS = 0
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 401)


if __name__ == "__main__":
    unittest.main()


class FakeGoogle:
    """Tiruan pertukaran kode Google: mengembalikan klaim yang sudah 'terverifikasi'."""
    def __init__(self):
        self.claims, self.raise_ = {}, False
        self.seen = {}

    def exchange(self, code, verifier, redirect_uri):
        self.seen = {"code": code, "verifier": verifier, "redirect": redirect_uri}
        if self.raise_:
            raise ValueError("invalid_grant")
        return dict(self.claims)


class TestGoogleLogin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = TestClient(app, follow_redirects=False)
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        self.saved = {k: getattr(config, k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ADMIN_EMAILS", "ADMIN_DOMAINS", "ADMIN_ALLOW_TOKEN")}
        config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET = "gid.apps.googleusercontent.com", "gsec"
        config.ADMIN_EMAILS, config.ADMIN_DOMAINS, config.ADMIN_ALLOW_TOKEN = ["wahyu@gmail.com"], [], False
        self.g = FakeGoogle()
        auth.set_google(self.g)
        self.c.cookies.clear()

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(config, k, v)
        auth.set_google(None)
        self.c.cookies.clear()

    def start(self):
        from urllib.parse import parse_qs, urlparse
        r = self.c.get("/auth/google/login")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
        self.assertEqual((q["code_challenge_method"], q["response_type"], q["client_id"]), ("S256", "code", config.GOOGLE_CLIENT_ID))
        return q

    def finish(self, q, **claims):
        self.g.claims = {"email": "wahyu@gmail.com", "email_verified": True, "name": "Wahyu", "nonce": q["nonce"], **claims}
        return self.c.get(f"/auth/google/callback?code=kode123&state={q['state']}")

    def test_disabled_when_not_configured(self):
        config.GOOGLE_CLIENT_ID = ""
        self.assertEqual(self.c.get("/auth/google/login").status_code, 404)
        self.assertFalse(self.c.get("/auth/me").json()["google"])

    def test_login_success_pkce_and_session(self):
        q = self.start()
        r = self.finish(q)
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/admin"))
        self.assertTrue(self.g.seen["redirect"].endswith("/auth/google/callback"))
        # PKCE: code_challenge = S256(code_verifier) yang dikirim saat penukaran kode
        import base64, hashlib
        want = base64.urlsafe_b64encode(hashlib.sha256(self.g.seen["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(q["code_challenge"], want)
        me = self.c.get("/auth/me").json()
        self.assertEqual((me["authed"], me["email"], me["via"], me["google"]), (True, "wahyu@gmail.com", "google", True))
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 200)

    def test_token_disabled_when_google_on(self):
        self.assertEqual(self.c.get("/api/admin/summary", headers=ADM).status_code, 401)
        self.assertFalse(self.c.get("/auth/me").json()["token_allowed"])

    def test_other_gmail_account_denied(self):
        self.assertEqual(self.finish(self.start(), email="orang.lain@gmail.com").headers["location"], "/admin?err=ditolak")
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 401)

    def test_unverified_email_denied(self):
        self.assertEqual(self.finish(self.start(), email_verified=False).headers["location"], "/admin?err=ditolak")

    def test_gmail_domain_never_grants_admin(self):
        config.ADMIN_EMAILS, config.ADMIN_DOMAINS = [], ["gmail.com"]     # salah konfigurasi yang berbahaya
        self.assertEqual(self.finish(self.start(), email="siapa.saja@gmail.com").headers["location"], "/admin?err=ditolak")

    def test_workspace_domain_via_hd_claim(self):
        config.ADMIN_EMAILS, config.ADMIN_DOMAINS = [], ["kampus.ac.id"]
        self.assertEqual(self.finish(self.start(), email="dosen@kampus.ac.id", hd="kampus.ac.id").headers["location"], "/admin")
        self.c.cookies.clear()
        self.assertEqual(self.finish(self.start(), email="palsu@kampus.ac.id.evil.com", hd="evil.com").headers["location"], "/admin?err=ditolak")

    def test_nonce_mismatch_and_state_single_use(self):
        q = self.start()
        self.assertEqual(self.finish(q, nonce="nonce-palsu").headers["location"], "/admin?err=gagal")
        self.assertEqual(self.c.get(f"/auth/google/callback?code=x&state={q['state']}").headers["location"], "/admin?err=kedaluwarsa")  # replay
        self.assertEqual(self.c.get("/auth/google/callback?code=x&state=palsu").headers["location"], "/admin?err=kedaluwarsa")

    def test_exchange_failure_and_user_cancel(self):
        self.g.raise_ = True
        self.assertEqual(self.finish(self.start()).headers["location"], "/admin?err=gagal")
        self.assertEqual(self.c.get("/auth/google/callback?error=access_denied").headers["location"], "/admin?err=dibatalkan")


class TestPasswordLogin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = TestClient(app, follow_redirects=False)
        cls.c = cls._ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def setUp(self):
        self.saved = (config.ADMIN_USER, config.ADMIN_PASSWORD_HASH)
        config.ADMIN_USER, config.ADMIN_PASSWORD_HASH = "adm", auth.hash_password("rahasia123", iters=1000)
        auth._FAILS.clear()
        self.c.cookies.clear()

    def tearDown(self):
        config.ADMIN_USER, config.ADMIN_PASSWORD_HASH = self.saved
        self.c.cookies.clear()

    def test_success_and_wrong_and_lockout(self):
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 401)
        self.assertEqual(self.c.post("/auth/password", json={"username": "adm", "password": "salah"}).status_code, 401)
        self.assertEqual(self.c.post("/auth/password", json={"username": "x", "password": "rahasia123"}).status_code, 401)
        self.assertEqual(self.c.post("/auth/password", json={"username": "adm", "password": "rahasia123"}).status_code, 200)
        self.assertEqual(self.c.get("/api/admin/summary").status_code, 200)
        auth._FAILS.clear()
        self.c.cookies.clear()
        for _ in range(5):
            self.c.post("/auth/password", json={"username": "adm", "password": "salah"})
        self.assertEqual(self.c.post("/auth/password", json={"username": "adm", "password": "rahasia123"}).status_code, 429)

    def test_disabled_when_unset(self):
        config.ADMIN_PASSWORD_HASH = ""
        self.assertEqual(self.c.post("/auth/password", json={"username": "adm", "password": "x"}).status_code, 404)
