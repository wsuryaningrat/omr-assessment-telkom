function admin() {
  return {
    regradeKelas: "", regradeRes: null, token: "", authed: false, loginErr: "", ready: false, errMsg: "", me: { microsoft: false, google: false, authed: false, token_allowed: true }, tab: "ringkasan", busy: false, kelas: "",
    tabs: [{ id: "ringkasan", label: "Ringkasan" }, { id: "sesi", label: "Sesi" }, { id: "kunci", label: "Kunci jawaban" }, { id: "ekspor", label: "Ekspor" }],
    sum: { state: {} }, kunci: [], ses: { items: [], total: 0, page: 1, size: 25, q: "", status: "all" },
    msg: { text: "", bad: false, show: false }, _t: null, _poll: null,

    async init() {
      const ERR = { ditolak: "Akun ini tidak terdaftar sebagai admin.", tenant: "Akun berasal dari organisasi yang tidak diizinkan.",
        kedaluwarsa: "Sesi masuk kedaluwarsa. Silakan coba lagi.", gagal: "Login gagal. Silakan coba lagi.", dibatalkan: "Login dibatalkan.",
        konfigurasi: "Login belum dikonfigurasi dengan benar atau penyedia login tidak terjangkau. Hubungi pengelola server." };
      const q = new URLSearchParams(location.search).get("err");
      if (q) { this.errMsg = ERR[q] || "Login gagal."; history.replaceState(null, "", "/admin"); }
      try { this.me = await (await fetch("/auth/me")).json(); } catch {}
      if (this.me.authed) { await this.login(true); }
      else {
        try { this.token = sessionStorage.getItem("adm_tok") || ""; } catch {}
        if (this.token && this.me.token_allowed) await this.login(true);
      }
      this.ready = true;
    },
    toast(text, bad = false) { this.msg = { text, bad, show: true }; clearTimeout(this._t); this._t = setTimeout(() => (this.msg.show = false), bad ? 3500 : 2200); },
    async api(path, opt = {}) {
      const h = { ...(opt.headers || {}) }; if (this.token) h["X-Admin-Token"] = this.token;
      const r = await fetch(path, { ...opt, headers: h, credentials: "same-origin" });
      if (r.status === 401) { this.logout(); throw new Error("Token tidak valid"); }
      if (!r.ok) { let d = ""; try { const j = await r.json(); d = Array.isArray(j.detail) ? j.detail.join("; ") : (j.detail || ""); } catch {} throw new Error(d || `Kesalahan ${r.status}`); }
      return r.status === 204 ? null : r;
    },
    async json(path, opt) { const r = await this.api(path, opt); return r ? r.json() : null; },
    async login(silent = false) {
      this.loginErr = "";
      try { this.sum = await this.json("/api/admin/summary"); this.authed = true; try { sessionStorage.setItem("adm_tok", this.token); } catch {} this.startPoll(); }
      catch (e) { this.authed = false; if (!silent) this.loginErr = "Token tidak valid."; }
    },
    async logout() {
      clearInterval(this._poll); this.authed = false; this.token = ""; try { sessionStorage.removeItem("adm_tok"); } catch {}
      if (this.me.authed) { try { await fetch("/auth/logout", { method: "POST" }); } catch {} this.me.authed = false; }
    },
    startPoll() { clearInterval(this._poll); this._poll = setInterval(() => { if (this.authed && this.tab === "ringkasan") this.loadSummary(); }, 5000); },
    async go(t) { this.tab = t; if (t === "sesi") await this.loadSessions(); if (t === "kunci") await this.loadKunci(); if (t === "ringkasan") await this.loadSummary(); },
    async loadSummary() { try { this.sum = await this.json("/api/admin/summary"); } catch {} },
    async loadSessions() {
      const s = this.ses, p = new URLSearchParams({ page: s.page, size: s.size, q: s.q, status: s.status });
      try { const d = await this.json("/api/admin/sessions?" + p); Object.assign(this.ses, { items: d.items, total: d.total }); } catch (e) { this.toast(e.message, true); }
    },
    async loadKunci() { try { this.kunci = await this.json("/api/admin/kunci"); } catch (e) { this.toast(e.message, true); } },
    async act(path, okMsg) {
      this.busy = true;
      try { const d = await this.json(path, { method: "POST" }); this.toast(okMsg + (d && d.synced !== undefined ? ` (${d.synced} sesi terkirim)` : "")); await this.loadSummary(); }
      catch (e) { this.toast(e.message, true); }
      this.busy = false;
    },
    async uploadKunci(ev) {
      const f = ev.target.files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f, f.name);
      try { const d = await this.json("/api/admin/kunci/upload", { method: "POST", body: fd }); this.toast("Kunci diunggah: " + Object.keys(d.kunci).join(", ")); await this.loadKunci(); }
      catch (e) { this.toast(e.message, true); }
      ev.target.value = "";
    },
    async regrade(pull) {
      if (!confirm(pull ? "Tarik kunci dari Google Sheet lalu hitung ulang nilai semua sesi yang sudah disubmit?" : "Hitung ulang nilai semua sesi yang sudah disubmit dengan kunci saat ini?")) return;
      this.busy = true; this.regradeRes = null;
      try {
        const q = new URLSearchParams({ kelas: this.regradeKelas, pull: pull ? "true" : "false" });
        this.regradeRes = await this.json("/api/admin/regrade?" + q, { method: "POST" });
        if (!this.regradeRes.error) this.toast(`Selesai — ${this.regradeRes.changed} lembar berubah`);
        await this.loadKunci();
      } catch (e) { this.toast(e.message, true); }
      this.busy = false;
    },
    async delKunci(name) {
      if (!confirm(`Hapus kunci "${name}"?`)) return;
      try { await this.api("/api/admin/kunci/" + encodeURIComponent(name), { method: "DELETE" }); this.toast("Kunci dihapus"); await this.loadKunci(); }
      catch (e) { this.toast(e.message, true); }
    },
    async download(path, filename) {
      try {
        const r = await this.api(path), b = await r.blob(), a = document.createElement("a");
        a.href = URL.createObjectURL(b); a.download = filename; document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      } catch (e) { this.toast(e.message, true); }
    },
    fmt(t) { if (!t) return "-"; try { return new Date(t).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" }); } catch { return t; } },
  };
}
