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
