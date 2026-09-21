const PHONE = v => { const d = (v || "").replace(/[\s\-()+]/g, ""); return /^\d{9,15}$/.test(d); };

function ljk() {
  return {
    meta: {}, form: { nama: "", hp: "", ruangan: "", kelas: "", fakultas: "", prodi: "" }, view: null, baseline: "", page: 0, pageSize: 10,
    ready: false, sid: null, session: null, sel: null, filter: "all", q: "", online: true, dragging: false, busy: false,
    up: { done: 0, total: 0 }, pv: { url: "", loading: false }, msg: { text: "", bad: false },
    _poll: null, _toast: null,

    async init() {
      try { this.meta = await (await fetch("/api/meta")).json(); } catch { this.online = false; this.toast("Server tidak terjangkau", true); }
      try { this.sid = localStorage.getItem("ljk_sid"); } catch {}
      if (this.sid) { await this.refresh(true); this.loadForm(); }
      this.ready = true;
    },

    // ---- turunan
    get prodiOptions() { return (this.meta.fakultas_prodi || {})[this.form.fakultas] || []; },
    get phoneOk() { return PHONE(this.form.hp); },
    get formValid() { const f = this.form; return !!(f.nama && this.phoneOk && f.ruangan && f.kelas && f.fakultas && f.prodi); },
    get dirty() { return !!this.sid && JSON.stringify(this.form) !== this.baseline; },
    get formHint() {
      const f = this.form;
      if (!f.nama) return "Isi nama lengkap pengawas"; if (!this.phoneOk) return "Isi nomor HP yang valid";
      if (!f.ruangan) return "Isi ruangan"; if (!f.kelas) return "Isi nama kelas"; if (!f.fakultas) return "Pilih fakultas"; if (!f.prodi) return "Pilih program studi";
      return "Siap — pilih berkas LJK di atas";
    },
    get step() { if (this.session?.submitted) return 3; if (this.view) return this.view; return this.session?.summary.lembar ? 2 : 1; },
    canGo(n) { return !this.session?.submitted && this.step !== n && (n === 1 ? !!this.sid : !!this.session?.summary.lembar); },
    go(n) {
      if (!this.canGo(n)) return;
      if (n === 1) this.loadForm();
      this.view = n; this.close(); scrollTo({ top: 0 });
    },
    loadForm() {
      const p = this.session?.pengawas; if (!p) return;
      this.form = { nama: p.nama, hp: p.hp, ruangan: p.ruangan, kelas: p.kelas || "", fakultas: p.fakultas, prodi: p.prodi };
      this.baseline = JSON.stringify(this.form);
    },
    async saveIdentity() {
      if (!this.sid || !this.formValid) return false;
      const f = this.form;
      try {
        await this.api(`/api/sessions/${this.sid}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nama_pengawas: f.nama, hp: f.hp, ruangan: f.ruangan, kelas: f.kelas, fakultas: f.fakultas, prodi: f.prodi }) });
        this.baseline = JSON.stringify(this.form); await this.refresh(); this.toast("Data pengawas disimpan"); return true;
      } catch (e) { this.toast(e.message, true); return false; }
    },
    npmCells(s) {
      const t = (s.npm && s.npm !== "-") ? String(s.npm) : "";
      return Array.from({ length: 10 }, (_, i) => { const c = t[i] || ""; return c.trim(); });
    },
    npmCount(s) { return this.npmCells(s).filter(c => /^\d$/.test(c)).length; },
    get scanDone() { return this.session ? this.session.files.total - this.session.files.pending : 0; },
    get scanPct() { const t = this.session?.files.total || 0; return t ? this.scanDone / t * 100 : 0; },
    get allValid() { const s = this.session?.summary; return !!s && s.lembar > 0 && s.ok === s.lembar; },
    get canSubmit() { return this.allValid && !this.session.scanning; },
    get facPick() { return (this.session?.pengawas?.fakultas || "").split(" - ")[0]; },
    facMatch(s) {
      const v = (s.fakultas_ljk || "").toString().trim().toUpperCase();
      return !!this.facPick && !!v && v !== "-" && v.startsWith(this.facPick.toUpperCase());
    },
    fillPct(s) { const m = /(\d+)\s*\/\s*(\d+)/.exec(s.terisi || ""); return m && +m[2] ? Math.round(+m[1] / +m[2] * 100) : 0; },
    nameOf(s) { return s.nama && s.nama !== "-" ? s.nama : "(nama tidak terbaca)"; },
    get filtered() {
      let l = this.session?.sheets || [];
      if (this.filter === "todo") l = l.filter(s => s.label === "Perlu Validasi");
      else if (this.filter === "ok") l = l.filter(s => s.label === "OK");
      else if (this.filter === "bad") l = l.filter(s => s.label === "Gagal");
      const q = this.q.toLowerCase();
      return q ? l.filter(s => `${s.nama} ${s.npm} ${s.file}`.toLowerCase().includes(q)) : l;
    },
    numOf(s) { return (this.session?.sheets || []).findIndex(x => x.id === s.id) + 1; },
    get pageCount() { return Math.max(1, Math.ceil(this.filtered.length / this.pageSize)); },
    get curPage() { return Math.min(Math.max(0, this.page), this.pageCount - 1); },
    get paged() { const a = this.curPage * this.pageSize; return this.filtered.slice(a, a + this.pageSize); },
    get rangeText() {
      const n = this.filtered.length; if (!n) return "0 lembar";
      const a = this.curPage * this.pageSize; return `Menampilkan ${a + 1}–${Math.min(a + this.pageSize, n)} dari ${n} lembar`;
    },
    get pageTodo() { return this.paged.filter(s => s.label === "Perlu Validasi").length; },
    get pageAllValid() { return this.paged.length > 0 && this.paged.every(s => s.validated || s.label === "Gagal") && this.paged.some(s => s.validated); },
    toTable() { document.querySelector(".tools")?.scrollIntoView({ behavior: "smooth", block: "start" }); },
    async validatePage() {
      const want = !this.pageAllValid;
      const ids = this.paged.filter(s => want ? (!s.validated && s.label !== "Gagal") : s.validated).map(s => s.id);
      if (!ids.length) { this.toast("Tidak ada lembar yang perlu diubah"); return; }
      ids.forEach(id => this.apply(id, want));                 // optimistis
      try { await this.api("/api/sheets/validate-batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, value: want }) });
            this.toast(want ? `${ids.length} lembar divalidasi` : `Validasi ${ids.length} lembar dibatalkan`); }
      catch (e) { this.toast(e.message, true); await this.refresh(); }
    },
    get submittedAt() {
      const t = this.session?.submitted_at; if (!t) return "-";
      try { return new Date(t).toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" }); } catch { return t; }
    },
    rowCls(s) { return s.label === "OK" ? "ok" : s.label === "Gagal" ? "bad" : "warn"; },

    // ---- util
    toast(text, bad = false) {
      this.msg = { text, bad }; clearTimeout(this._toast);
      this._toast = setTimeout(() => (this.msg = { text: "", bad: false }), 2600);
    },
    async api(path, opt = {}) {
      const r = await fetch(path, opt);
      if (!r.ok) {
        let d = ""; try { const j = await r.json(); d = Array.isArray(j.detail) ? j.detail.join("; ") : (j.detail || ""); } catch {}
        throw new Error(d || `Kesalahan ${r.status}`);
      }
      return r.status === 204 ? null : r.json();
    },

    // ---- sesi & polling
    async refresh(initial = false) {
      try { this.session = await this.api(`/api/sessions/${this.sid}`); }
      catch (e) { if (initial) { this.forget(); } else { this.toast(e.message, true); } return; }
      if (this.session.scanning) this.poll();
    },
    poll() {
      if (this._poll) return;
      this._poll = setInterval(async () => {
        try { this.session = await this.api(`/api/sessions/${this.sid}`); } catch {}
        if (!this.session?.scanning) { clearInterval(this._poll); this._poll = null; }
      }, 1000);
    },
    forget() { try { localStorage.removeItem("ljk_sid"); } catch {} this.sid = null; this.session = null; },
    resetAll() {
      if (this.session && !this.session.submitted && this.session.summary.lembar && !confirm("Mulai evaluasi baru? Data yang belum disubmit akan ditinggalkan.")) return;
      clearInterval(this._poll); this._poll = null; this.forget(); this.sel = null; this.up = { done: 0, total: 0 };
      this.form = { nama: "", hp: "", ruangan: "", kelas: "", fakultas: "", prodi: "" }; this.baseline = ""; this.view = null; this.filter = "all"; scrollTo({ top: 0 });
    },

    // ---- unggah
    async pick(files) {
      files = [...(files || [])]; if (!files.length) return;
      if (!this.formValid) { this.toast(this.formHint, true); return; }
      if (this.dirty && !(await this.saveIdentity())) return;
      if (!this.sid) {
        try {
          const f = this.form;
          const r = await this.api("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nama_pengawas: f.nama, hp: f.hp, ruangan: f.ruangan, kelas: f.kelas, fakultas: f.fakultas, prodi: f.prodi }) });
          this.sid = r.id; this.baseline = JSON.stringify(this.form); try { localStorage.setItem("ljk_sid", this.sid); } catch {}
          await this.refresh();
        } catch (e) { this.toast(e.message, true); return; }
      }
      const max = (this.meta.max_upload_mb || 10) * 1048576;
      const ok = files.filter(f => f.size <= max);
      if (ok.length < files.length) this.toast(`${files.length - ok.length} berkas > ${this.meta.max_upload_mb} MB dilewati`, true);
      this.up = { done: 0, total: ok.length };
      let i = 0, fail = 0;
      const worker = async () => {
        while (i < ok.length) {
          const f = ok[i++], fd = new FormData(); fd.append("files", f, f.name);
          try { await this.api(`/api/sessions/${this.sid}/files`, { method: "POST", body: fd }); } catch (e) { fail++; this.toast(`${f.name}: ${e.message}`, true); }
          this.up.done++;
          if (this.up.done % 3 === 0 || this.up.done === ok.length) this.refresh();
        }
      };
      await Promise.all([worker(), worker(), worker()]);   // 3 unggahan paralel
      await this.refresh();
      if (!fail) this.toast(`${ok.length} berkas diunggah`);
    },

    // ---- aksi
    async toggle(s) {
      const want = !s.validated, prev = s.validated;
      this.apply(s.id, want);                                  // optimistis
      try { await this.api(`/api/sheets/${s.id}/${want ? "validate" : "unvalidate"}`, { method: "POST" }); }
      catch (e) { this.apply(s.id, prev); this.toast(e.message, true); }
    },
    apply(id, v) {
      const s = this.session.sheets.find(x => x.id === id); if (!s || s.validated === v) return;
      s.validated = v; s.label = v ? "OK" : "Perlu Validasi";
      const m = this.session.summary; m.ok += v ? 1 : -1; m.perlu_validasi += v ? -1 : 1;
    },
    async validateAll(value) {
      try { await this.api(`/api/sessions/${this.sid}/validate-all?value=${value}`, { method: "POST" }); await this.refresh(); }
      catch (e) { this.toast(e.message, true); }
    },
    async submit() {
      this.busy = true;
      try { await this.api(`/api/sessions/${this.sid}/submit`, { method: "POST" }); await this.refresh(); this.view = null; scrollTo({ top: 0 }); }
      catch (e) { this.toast(e.message, true); }
      this.busy = false;
    },
    open(s) { this.sel = s; this.pv = { url: "", loading: false }; document.body.style.overflow = "hidden"; },
    close() { this.sel = null; document.body.style.overflow = ""; },
    showPreview() { this.pv = { url: `/api/sheets/${this.sel.id}/preview?t=${Date.now()}`, loading: true }; },
    async remove(s) {
      if (!confirm(`Hapus lembar No. ${this.numOf(s)} (${this.nameOf(s)}) dari daftar?\nGunakan ini bila foto terunggah dobel.`)) return;
      try { await this.api(`/api/sheets/${s.id}`, { method: "DELETE" }); this.close(); await this.refresh(); this.toast("Lembar dihapus"); }
      catch (e) { this.toast(e.message, true); }
    },
    async replace(file) {
      if (!file) return;
      const fd = new FormData(); fd.append("file", file, file.name);
      this.toast("Memproses foto baru…");
      try { await this.api(`/api/sheets/${this.sel.id}/replace`, { method: "POST", body: fd }); await this.refresh();
            this.sel = this.session.sheets.find(x => x.id === this.sel.id) || null; this.pv = { url: "", loading: false }; this.toast("Foto diganti & dipindai ulang"); }
      catch (e) { this.toast(e.message, true); }
    },
  };
}
