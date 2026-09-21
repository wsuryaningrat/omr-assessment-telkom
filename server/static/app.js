// ---- log diagnostik ke server (membantu melacak masalah di ponsel)
function clientLog(ev, data) {
  try {
    const body = JSON.stringify({ ev, ua: navigator.userAgent.slice(0, 120), w: innerWidth, ...data });
    if (navigator.sendBeacon) navigator.sendBeacon("/api/clientlog", new Blob([body], { type: "application/json" }));
    else fetch("/api/clientlog", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true });
  } catch {}
}
function surfaceError(msg) {
  clientLog("error", { msg: String(msg).slice(0, 300) });
  try { const a = Alpine.$data(document.body); a.toast("Terjadi kesalahan: " + String(msg).slice(0, 120), true); } catch {}
}
addEventListener("error", e => surfaceError(e.message));
addEventListener("unhandledrejection", e => surfaceError(e.reason?.message || e.reason));

const PHONE = v => { const d = (v || "").replace(/[\s\-()+]/g, ""); return /^\d{9,15}$/.test(d); };

function ljk() {
  return {
    meta: {}, form: { nama: "", hp: "", ruangan: "", kelas: "", fakultas: "", prodi: "" }, view: null, baseline: "", page: 0, pageSize: 10,
    ready: false, sid: null, session: null, sel: null, filter: "all", q: "", online: true, dragging: false, busy: false,
    upErr: "", upStatus: "", dlg: null, _dlgRes: null, up: { done: 0, total: 0 }, pv: { url: "", loading: false }, msg: { text: "", bad: false },
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
        const facChanged = this.baseline && JSON.parse(this.baseline).fakultas !== f.fakultas;
        this.baseline = JSON.stringify(this.form); await this.refresh();
        // status dihitung ulang dari data terbaru; lembar yang sudah divalidasi tapi kini tidak sesuai fakultas dikembalikan ke Warning
        const stale = facChanged ? (this.session?.sheets || []).filter(s => s.validated && this.fakBad(s)) : [];
        if (stale.length) {
          try { await this.api("/api/sheets/validate-batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: stale.map(s => s.id), value: false }) }); await this.refresh(); } catch {}
          this.toast(`Data disimpan — ${stale.length} lembar perlu dicek ulang karena fakultas berbeda`, true);
        } else this.toast("Data pengawas disimpan");
        return true;
      } catch (e) { this.toast(e.message, true); return false; }
    },
    // ---- status & aturan peringatan
    npmDigits(s) { const t = (s.npm && s.npm !== "-") ? String(s.npm) : ""; return Array.from({ length: 10 }, (_, i) => t[i] || " "); },
    npmCount(s) { return this.npmDigits(s).filter(c => /^\d$/.test(c)).length; },
    npmText(s) { return this.npmCount(s) || (s.npm && s.npm !== "-") ? this.npmDigits(s).map(c => /^[\d?]$/.test(c) ? c : "·").join("") : "-"; },
    npmBad(s) { return this.npmCount(s) !== 10; },
    kodeBad(s) { return !/^\d{3}$/.test(String(s.kode_soal || "").trim()); },
    fakBad(s) { return !this.facMatch(s); },
    fakText(s) { const v = (s.fakultas_ljk || "").toString().trim(); return v && v !== "-" ? v : "(kosong)"; },
    fillBad(s) { return this.fillPct(s) < 25; },
    terisiText(s) { return (s.terisi || "-").toString().replace(/\s+/g, ""); },
    reasons(s) {
      if (s.label === "Gagal") return ["Pojok LJK tidak terdeteksi — ganti dengan foto baru"];
      const r = [];
      if (this.npmBad(s)) r.push(`NPM terbaca ${this.npmCount(s)} dari 10 digit`);
      if (this.kodeBad(s)) r.push(`Kode soal "${s.kode_soal || "-"}" bukan 3 digit`);
      if (this.fakBad(s)) r.push(this.fakText(s) === "(kosong)" ? "Fakultas tidak terisi" : `Fakultas ${this.fakText(s)} berbeda dari isian pengawas (${this.facPick})`);
      if (this.fillBad(s)) r.push(`Jawaban terisi rendah (${this.terisiText(s)}, di bawah 25%)`);
      return r;
    },
    reasonsShort(s) {
      if (s.label === "Gagal") return "Pojok LJK tidak terdeteksi";
      const r = [];
      if (this.npmBad(s)) r.push(`NPM ${this.npmCount(s)}/10`);
      if (this.kodeBad(s)) r.push("Kode ≠ 3 digit");
      if (this.fakBad(s)) r.push(this.fakText(s) === "(kosong)" ? "Fak. kosong" : `Fak. ≠ ${this.facPick}`);
      if (this.fillBad(s)) r.push("Terisi rendah");
      return r.join(" · ");
    },
    status(s) { return s.validated ? "validated" : (this.reasons(s).length ? "warning" : "pending"); },
    statusText(s) { return { validated: "Validated", warning: "Warning", pending: "Checking" }[this.status(s)]; },
    get cnt() {
      const c = { total: 0, pending: 0, warning: 0, validated: 0 };
      for (const s of this.session?.sheets || []) { c.total++; c[this.status(s)]++; }
      return c;
    },
    setFilter(f) { this.filter = this.filter === f ? "all" : f; this.page = 0; },
    ask(o) { return new Promise(res => { this.dlg = { ok: "OK", cancel: "Batal", danger: false, ...o }; this._dlgRes = res; }); },
    answer(v) { const r = this._dlgRes; this.dlg = null; this._dlgRes = null; if (r) r(v); },
    async confirmForce(list) {
      const one = list.length === 1;
      return this.ask({
        title: one ? "Lembar ini memiliki peringatan" : `${list.length} lembar memiliki peringatan`,
        body: "Disarankan periksa ulang lembar fisiknya atau ambil foto ulang sebelum divalidasi. Tetap validasi?",
        list: one ? this.reasons(list[0]) : list.slice(0, 5).map(s => `No. ${this.numOf(s)} ${this.nameOf(s)} — ${this.reasonsShort(s)}`).concat(list.length > 5 ? [`…dan ${list.length - 5} lainnya`] : []),
        ok: "Tetap validasi", cancel: "Cek ulang", danger: true,
      });
    },
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
      if (this.filter !== "all") l = l.filter(s => this.status(s) === this.filter);
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
    get pageTodo() { return this.paged.filter(s => !s.validated && s.label !== "Gagal").length; },
    get pageAllValid() { return this.paged.length > 0 && this.paged.every(s => s.validated || s.label === "Gagal") && this.paged.some(s => s.validated); },
    toTable() { document.querySelector(".tools")?.scrollIntoView({ behavior: "smooth", block: "start" }); },
    async validatePage() {
      const want = !this.pageAllValid;
      const ids = this.paged.filter(s => want ? (!s.validated && s.label !== "Gagal") : s.validated).map(s => s.id);
      if (!ids.length) { this.toast("Tidak ada lembar yang perlu diubah"); return; }
      if (want) {
        const warn = this.paged.filter(s => ids.includes(s.id) && this.status(s) === "warning");
        if (warn.length && !(await this.confirmForce(warn))) return;
      }
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
    guardPick(ev) {
      // Bila isian belum lengkap: batalkan pembukaan pemilih berkas dan tampilkan alasan langsung di layar.
      if (this.formValid) { this.upErr = ""; return; }
      ev.preventDefault(); this.upErr = "Lengkapi data pengawas & kelas dulu: " + this.formHint.toLowerCase() + ".";
      clientLog("guard", { hint: this.formHint });
      document.getElementById(!this.form.nama ? "nama" : !this.phoneOk ? "hp" : !this.form.ruangan ? "rg" : !this.form.kelas ? "kl" : !this.form.fakultas ? "fk" : "pr")?.focus();
    },
    onFiles(ev) {
      const input = ev.target, files = Array.from(input.files || []);
      clientLog("pick", { n: files.length, valid: this.formValid, sid: !!this.sid, types: files.slice(0, 3).map(f => f.type || f.name.split(".").pop()), sizes: files.slice(0, 3).map(f => f.size) });
      this.upStatus = files.length ? `${files.length} berkas dipilih…` : "Tidak ada berkas yang terbaca — coba pilih lagi.";
      if (!files.length) return;
      this.pick(files).finally(() => { try { input.value = ""; } catch {} });
    },
    onReplace(ev) { const input = ev.target, f = input.files && input.files[0]; if (f) this.replace(f).finally(() => { try { input.value = ""; } catch {} }); },
    async pick(files) {
      files = [...(files || [])]; if (!files.length) return;
      if (!this.formValid) { this.upErr = "Lengkapi data dulu: " + this.formHint.toLowerCase() + "."; this.toast(this.formHint, true); return; }
      this.upErr = "";
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
      this.up = { done: 0, total: ok.length }; this.upStatus = `Mengunggah ${ok.length} berkas…`;
      clientLog("upload_start", { n: ok.length });
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
      this.upStatus = fail ? `${ok.length - fail} berkas terunggah, ${fail} gagal.` : `${ok.length} berkas terunggah — sedang dipindai…`;
      clientLog("upload_done", { ok: ok.length - fail, fail });
      if (!fail) this.toast(`${ok.length} berkas diunggah`);
    },

    // ---- aksi
    async toggle(s) {
      const want = !s.validated, prev = s.validated;
      if (want && this.status(s) === "warning" && !(await this.confirmForce([s]))) return;
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
      if (value) {
        const warn = (this.session?.sheets || []).filter(s => this.status(s) === "warning" && s.label !== "Gagal");
        if (warn.length && !(await this.confirmForce(warn))) return;
      }
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
      if (!(await this.ask({ title: `Hapus lembar No. ${this.numOf(s)}?`, body: `${this.nameOf(s)} (${s.file}). Gunakan ini bila foto terunggah dobel.`, ok: "Hapus", cancel: "Batal", danger: true }))) return;
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
