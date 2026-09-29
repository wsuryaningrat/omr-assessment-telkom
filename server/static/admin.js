function admin() {
  return {
    regradeKelas: "", regradeRes: null, token: "", usr: "", pwd: "", pwErr: "", pwBusy: false, authed: false, loginErr: "", ready: false, errMsg: "", me: { password: false, microsoft: false, google: false, authed: false, token_allowed: true }, tab: "monitoring", busy: false, kelas: "", mon: null, monDay: "", monFilter: "all", monQ: "", monBusy: false, monLuar: false,
    tabs: [{ id: "monitoring", label: "Monitoring" }, { id: "ringkasan", label: "Ringkasan" }, { id: "sesi", label: "Sesi" }, { id: "kunci", label: "Kunci jawaban" }, { id: "ekspor", label: "Ekspor" }],
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
    async pwLogin() {
      this.pwErr = ""; this.pwBusy = true;
      try {
        const r = await fetch("/auth/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: this.usr, password: this.pwd }) });
        if (!r.ok) { this.pwErr = (await r.json().catch(() => ({}))).detail || "Gagal masuk."; }
        else { this.pwd = ""; this.me = await (await fetch("/auth/me")).json(); await this.login(true); }
      } catch { this.pwErr = "Server tidak terjangkau."; }
      this.pwBusy = false;
    },
    async login(silent = false) {
      this.loginErr = "";
      try { this.sum = await this.json("/api/admin/summary"); this.authed = true; try { sessionStorage.setItem("adm_tok", this.token); } catch {} this.startPoll(); await this.loadMonitor(); }
      catch (e) { this.authed = false; if (!silent) this.loginErr = "Token tidak valid."; }
    },
    async logout() {
      clearInterval(this._poll); this.authed = false; this.token = ""; try { sessionStorage.removeItem("adm_tok"); } catch {}
      if (this.me.authed) { try { await fetch("/auth/logout", { method: "POST" }); } catch {} this.me.authed = false; }
    },
    startPoll() { clearInterval(this._poll); this._poll = setInterval(() => { if (!this.authed) return; if (this.tab === "ringkasan") this.loadSummary(); if (this.tab === "sesi") this.loadSessions(); }, 5000); clearInterval(this._monPoll); this._monPoll = setInterval(() => { if (this.authed && this.tab === "monitoring") this.loadMonitor(); }, 30000); },
    async loadMonitor(refresh = false) {
      this.monBusy = true;
      try {
        const d = await this.json("/api/admin/monitor?mode=onsite" + (refresh ? "&refresh=true" : ""));
        this.mon = d;
        if (!this.monDay || !d.days.some(x => x.hari === this.monDay)) {
          const today = d.days.find(x => x.hari === d.hari_ini), open = d.days.find(x => x.selesai < x.total);
          this.monDay = (today || open || d.days[0] || {}).hari || "";
        }
      } catch (e) { this.toast(e.message, true); }
      this.monBusy = false;
    },
    get monDayData() { return (this.mon?.days || []).find(x => x.hari === this.monDay) || null; },
    get monSlots() {
      const d = this.monDayData; if (!d) return [];
      const q = this.monQ.trim().toLowerCase();
      return d.slots.filter(x => (this.monFilter === "all" || x.status === this.monFilter) && (!q || (x.kelas + " " + x.pengawas + " " + x.prodi + " " + x.ruangan + " " + x.gedung).toLowerCase().includes(q)));
    },
    pct(n, t) { return t ? Math.min(100, Math.round(n / t * 100)) : 0; },
    monLabel(st) { return { selesai: "Selesai", berjalan: "Berjalan", belum: "Belum" }[st] || st; },
    monWhen(iso) { if (!iso) return ""; try { return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); } catch { return ""; } },
    async go(t) { this.tab = t; if (t === "monitoring") await this.loadMonitor(); if (t === "sesi") await this.loadSessions(); if (t === "kunci") await this.loadKunci(); if (t === "ringkasan") await this.loadSummary(); },
    async loadSummary() { try { this.sum = await this.json("/api/admin/summary"); } catch {} },
    async loadSessions() {
      const s = this.ses, p = new URLSearchParams({ page: s.page, size: s.size, q: s.q, status: s.status });
      try { const d = await this.json("/api/admin/sessions?" + p); Object.assign(this.ses, { items: d.items, total: d.total }); } catch (e) { this.toast(e.message, true); }
    },
    sesStatusLabel(st) { return { scanning: "Memindai…", perlu_cek: "Perlu dicek", validated: "Validated" }[st] || st; },
    hariLabel(v) { if (!v) return "-"; const d = new Date(v + "T00:00:00"); return isNaN(d) ? v : d.toLocaleDateString("id-ID", { weekday: "short", day: "2-digit", month: "short" }); },
    stripKode(k) { const m = /^k[j]?(\d+)$/i.exec(k || ""); return m ? m[1] : (k || "-"); },
    sesStatusChip(st) { return { scanning: "pending", perlu_cek: "check", validated: "validated" }[st] || ""; },
    async validateSession(id, value) {
      if (value && !confirm("Tandai sesi ini validated? Semua lembar yang berhasil discan akan otomatis divalidasi & sesi dikunci (submit) supaya masuk ekspor/sinkron Sheet.")) return;
      try { const d = await this.json(`/api/admin/sessions/${id}/validate?value=${value}`, { method: "POST" }); this.toast(value ? "Sesi divalidasi & disubmit" : "Tanda validated dibatalkan"); await this.loadSessions(); }
      catch (e) { this.toast(e.message, true); }
    },
    async stopSession(id) {
      if (!confirm("Hentikan pemindaian sesi ini? Berkas yang belum mulai diproses akan dibatalkan; yang sudah berjalan tetap diselesaikan.")) return;
      try { const d = await this.json(`/api/admin/sessions/${id}/stop`, { method: "POST" }); this.toast(`Dihentikan — ${d.dibatalkan} dibatalkan, ${d.masih_berjalan} masih berjalan`); await this.loadSessions(); }
      catch (e) { this.toast(e.message, true); }
    },
    async deleteSession(id) {
      if (!confirm("Hapus sesi ini beserta semua lembar & berkasnya? Tindakan ini tidak bisa dibatalkan.")) return;
      try { await this.api(`/api/admin/sessions/${id}`, { method: "DELETE" }); this.toast("Sesi dihapus"); await this.loadSessions(); }
      catch (e) { this.toast(e.message, true); }
    },
    async clearPhotos(id) {
      if (!confirm("Hapus foto asli sesi ini dari server? Rekap/nilai TETAP tersimpan, hanya berkas foto sumbernya yang dihapus (tidak bisa dibatalkan).")) return;
      try { await this.json(`/api/admin/sessions/${id}/clear-photos`, { method: "POST" }); this.toast("Foto dibersihkan"); await this.loadSessions(); }
      catch (e) { this.toast(e.message, true); }
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
