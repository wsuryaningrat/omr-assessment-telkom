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
  if (/ResizeObserver|Script error|Load failed|Failed to fetch|NetworkError|AbortError/i.test(String(msg))) return;   // sepele / jaringan sesaat
  try { const a = Alpine.$data(document.body); a.toast("Terjadi kesalahan: " + String(msg).slice(0, 120), true); } catch {}
}
addEventListener("error", e => surfaceError(e.message));
addEventListener("unhandledrejection", e => surfaceError(e.reason?.message || e.reason));

// Nomor HP: awalan +62 dikunci di UI; pengguna hanya mengisi digit setelahnya (nomor seluler diawali 8, total 9–12 digit).
const PHONE = v => /^8\d{8,11}$/.test(v || "");
const hpLocal = raw => { let d = String(raw || "").replace(/\D/g, ""); if (d.startsWith("62")) d = d.slice(2); return d.replace(/^0+/, ""); };

function ljk() {
  return {
    meta: {}, form: { ref: "", nama: "", hp: "", kelas: "", kelasManual: "", prodi: "", prodiManual: "", fakultas: "", hari: "", kodeSoal: "" }, view: null, baseline: "",
    ready: false, sid: null, session: null, online: true, dragging: false, busy: false,
    upErr: "", upStatus: "", staged: [], _stagedSeq: 0, _replaceExisting: false, uploading: false, dlg: null, _dlgRes: null, up: { done: 0, total: 0 }, msg: { text: "", bad: false, show: false }, _dlgAt: 0,
    _poll: null, _toast: null,

    async init() {
      try { this.meta = await (await fetch("/api/meta")).json(); } catch { this.online = false; this.toast("Server tidak terjangkau", true); }
      try { this.sid = localStorage.getItem("ljk_sid"); } catch {}
      if (this.sid) { await this.refresh(true); this.loadForm(); }
      this.ready = true;
    },

    // ---- turunan
    get pengawasList() { return this.meta.pengawas || []; },
    get pengawasMhs() { return this.pengawasList.filter(p => !p.dosen); },
    get pengawasDosen() { return this.pengawasList.filter(p => p.dosen); },
    get picked() { return this.pengawasList.find(p => p.id === this.form.ref) || null; },
    get manual() { return this.form.ref === "manual"; },
    get showHp() { return this.manual || !!this.picked?.needs_hp; },
    get namaOk() { return this.manual ? !!this.form.nama : !!this.picked; },
    hariLabel(v) { return (this.meta.hari || []).find(h => h.value === v)?.label || ""; },
    stripKode(k) { const m = /^k[j]?(\d+)$/i.exec(k || ""); return m ? m[1] : (k || ""); },
    get kelasInfo() { return (this.meta.kelas || []).find(x => x.kelas === this.form.kelas) || null; },
    get prodiFinal() { return this.kelasInfo ? this.kelasInfo.prodi : (this.form.kelas === "manual" ? this.form.prodiManual.trim() : ""); },
    get fakultasFinal() { return this.kelasInfo ? (this.kelasInfo.fakultas || "") : (this.form.kelas === "manual" ? this.form.fakultas : ""); },
    get kelasFinal() { return this.form.kelas === "manual" ? this.form.kelasManual.trim() : this.form.kelas; },
    get kelasOptions() { return this.meta.kelas || []; },
    onPengawas() { if (!this.showHp) this.form.hp = ""; if (!this.manual) this.form.nama = ""; },
    get phoneOk() { return PHONE(this.form.hp); },
    cleanHp() { this.form.hp = hpLocal(this.form.hp).slice(0, 12); },
    identityBody() { const f = this.form; return JSON.stringify({ pengawas_ref: this.manual ? "" : f.ref, nama_pengawas: this.manual ? f.nama : "", hp: this.showHp ? "+62" + f.hp : "", kelas: this.kelasFinal, prodi: this.prodiFinal, fakultas: this.fakultasFinal, hari_ujian: f.hari, kode_soal: "" }); },
    get formValid() { const f = this.form; return !!(this.namaOk && (!this.showHp || this.phoneOk) && this.fakultasFinal && this.prodiFinal && this.kelasFinal && f.hari); },
    get dirty() { return !!this.sid && JSON.stringify(this.form) !== this.baseline; },
    get formHint() {
      const f = this.form;
      if (!f.ref) return "Pilih nama pengawas"; if (this.manual && !f.nama) return "Isi nama lengkap pengawas"; if (this.showHp && !this.phoneOk) return "Isi nomor HP yang valid";
      if (!this.kelasFinal) return f.kelas === "manual" ? "Isi nama kelas" : "Pilih kelas";
      if (!this.prodiFinal) return "Isi program studi"; if (!this.fakultasFinal) return f.kelas === "manual" ? "Pilih fakultas" : "Fakultas kelas ini belum terdaftar — hubungi admin";
      if (!f.hari) return "Pilih hari ujian";
      return "Siap — pilih berkas LJK di atas";
    },
    get step() { if (this.view) return this.view; return this.session?.summary.lembar ? 2 : 1; },
    canGo(n) { return this.step !== n && (n === 1 ? !!this.sid : !!this.session?.summary.lembar); },
    go(n) {
      if (!this.canGo(n)) return;
      if (n === 1) this.loadForm();
      this.view = n; scrollTo({ top: 0 });
    },
    loadForm() {
      const p = this.session?.pengawas; if (!p) return;
      const ref = this.pengawasList.find(x => x.nama === p.nama);
      const kn = (this.meta.kelas || []).some(x => x.kelas === p.kelas);
      this.form = { ref: ref ? ref.id : "manual", nama: ref ? "" : p.nama, hp: !ref || ref.needs_hp ? hpLocal(p.hp) : "",
        kelas: kn ? p.kelas : (p.kelas ? "manual" : ""), kelasManual: kn ? "" : (p.kelas || ""), prodi: "", prodiManual: kn ? "" : (p.prodi || ""),
        fakultas: kn ? "" : (p.fakultas || ""), hari: p.hari_ujian || "", kodeSoal: "" };
      this.baseline = JSON.stringify(this.form);
    },
    async saveIdentity() {
      if (!this.sid || !this.formValid) return false;
      const f = this.form;
      try {
        await this.api(`/api/sessions/${this.sid}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
          body: this.identityBody() });
        this.baseline = JSON.stringify(this.form); await this.refresh();
        this.toast("Data pengawas disimpan");
        return true;
      } catch (e) { this.toast(e.message, true); return false; }
    },
    // ---- dialog konfirmasi (dipakai confirmKelasNotDuplicate saat unggah -- lihat bawah)
    ask(o) { return new Promise(res => { this._dlgAt = Date.now(); this.dlg = { ok: "OK", cancel: "Batal", danger: false, ...o }; this._dlgRes = res; clientLog("dialog_open", { t: o.title }); }); },
    veil() { if (Date.now() - this._dlgAt > 500) this.answer(false); },
    answer(v) { const r = this._dlgRes; this.dlg = null; this._dlgRes = null; if (r) r(v); },
    get scanDone() { return this.session ? this.session.files.total - this.session.files.pending : 0; },
    get scanPct() { const t = this.session?.files.total || 0; return t ? this.scanDone / t * 100 : 0; },

    // ---- util
    toast(text, bad = false) {
      this.msg = { text, bad, show: true }; clearTimeout(this._toast);
      if (bad) clientLog("toast_bad", { msg: String(text).slice(0, 200) });
      this._toast = setTimeout(() => { this.msg.show = false; }, bad ? 3200 : 2200);   // teks dibiarkan agar tidak muncul kotak kosong saat memudar
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
      catch (e) { if (initial) { this.forget(); } else { clientLog("refresh_fail", { msg: String(e.message).slice(0, 120) }); } return; }
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
      clearInterval(this._poll); this._poll = null; this.forget(); this.clearStaged(); this.up = { done: 0, total: 0 };
      this.form = { ref: "", nama: "", hp: "", kelas: "", kelasManual: "", prodi: "", prodiManual: "", fakultas: "", hari: "", kodeSoal: "" }; this.baseline = ""; this.view = null; scrollTo({ top: 0 });
    },

    // ---- unggah
    guardPick(ev) {
      // Bila isian belum lengkap: batalkan pembukaan pemilih berkas dan tampilkan alasan langsung di layar.
      if (this.formValid) { this.upErr = ""; return; }
      ev.preventDefault(); this.upErr = "Lengkapi data pengawas & kelas dulu: " + this.formHint.toLowerCase() + ".";
      clientLog("guard", { hint: this.formHint });
      document.getElementById(!this.form.ref ? "pw" : (this.manual && !this.form.nama) ? "nama" : (this.showHp && !this.phoneOk) ? "hp" : !this.kelasFinal ? (this.form.kelas === "manual" ? "klm" : "kl") : !this.prodiFinal ? "prm" : !this.fakultasFinal ? "fk" : "hr")?.focus();
    },
    onFiles(ev) {
      const input = ev.target, files = Array.from(input.files || []);
      clientLog("pick", { n: files.length, valid: this.formValid, sid: !!this.sid, types: files.slice(0, 3).map(f => f.type || f.name.split(".").pop()), sizes: files.slice(0, 3).map(f => f.size) });
      if (!files.length) this.upStatus = "Tidak ada berkas yang terbaca — coba pilih lagi.";
      if (!files.length) return;
      this.pick(files); try { input.value = ""; } catch {}
    },
    // Foto sumber kini disimpan per Fakultas/Prodi/Kelas (dibagi antar sesi sekelas) -- cek dulu apakah
    // kombinasi ini sudah pernah diunggah sesi LAIN, supaya pengawas sadar sebelum menambah foto ke tempat
    // yang sama (tak ada apa pun yang dihapus/ditimpa di server -- ini murni pemberitahuan).
    async confirmKelasNotDuplicate() {
      const f = this.form;
      try {
        const q = new URLSearchParams({ fakultas: this.fakultasFinal, prodi: this.prodiFinal, kelas: this.kelasFinal });
        const r = await fetch("/api/kelas-check?" + q);
        const d = await r.json();
        if (!d.exists) return true;
        const yes = await this.ask({
          title: "Kelas sudah pernah diupload",
          body: "LJK kelas terpilih sudah terupload sebelumnya. Upload ulang akan menimpa sesi upload sebelumnya. Yakin?",
          ok: "Yakin", cancel: "Batal",
        });
        this._replaceExisting = !!yes;   // dikonfirmasi -> server menghapus sesi lama kelas ini saat sesi baru dibuat
        return yes;
      } catch { return true; }   // gagal cek -> jangan blokir unggah krn hal sepele
    },
    // Berkas yang dipilih TIDAK langsung dikirim: masuk daftar `staged` dulu supaya pengawas bisa membuang
    // yang tak sengaja terpilih, lalu menekan "Unggah" (uploadStaged) -- jumlah yang terkirim jadi tepat.
    pick(files) {
      files = [...(files || [])]; if (!files.length) return;
      const max = (this.meta.max_upload_mb || 10) * 1048576;
      const big = files.filter(f => f.size > max);
      if (big.length) this.toast(`${big.length} berkas > ${this.meta.max_upload_mb || 10} MB dilewati`, true);
      const key = f => `${f.name}|${f.size}|${f.lastModified}`;
      const have = new Set(this.staged.map(x => key(x.file)));
      let dup = 0;
      for (const f of files) {
        if (f.size > max) continue;
        if (have.has(key(f))) { dup++; continue; }
        have.add(key(f));
        const img = /^image\//.test(f.type) && !/heic|heif/i.test(f.type + f.name);
        this.staged.push({ id: ++this._stagedSeq, file: f, url: img ? URL.createObjectURL(f) : "" });
      }
      if (dup) this.toast(`${dup} berkas sama sudah ada di daftar`, true);
      this.upStatus = this.staged.length ? `${this.staged.length} berkas siap diunggah — periksa daftar lalu tekan Unggah.` : "";
    },
    removeStaged(id) {
      const i = this.staged.findIndex(x => x.id === id); if (i < 0) return;
      if (this.staged[i].url) URL.revokeObjectURL(this.staged[i].url);
      this.staged.splice(i, 1);
      this.upStatus = this.staged.length ? `${this.staged.length} berkas siap diunggah — periksa daftar lalu tekan Unggah.` : "";
    },
    clearStaged() { this.staged.forEach(x => x.url && URL.revokeObjectURL(x.url)); this.staged = []; this.upStatus = ""; },
    fmtSize(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; },
    async uploadStaged() {
      if (this.uploading || !this.staged.length) return;
      if (!this.formValid) { this.upErr = "Lengkapi data dulu: " + this.formHint.toLowerCase() + "."; this.toast(this.formHint, true); return; }
      this.upErr = "";
      if (this.dirty && !(await this.saveIdentity())) return;
      if (!this.sid) {
        if (!(await this.confirmKelasNotDuplicate())) return;
        try {
          const body = this._replaceExisting ? JSON.stringify({ ...JSON.parse(this.identityBody()), replace_existing: true }) : this.identityBody();
          const r = await this.api("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body });
          this._replaceExisting = false;
          this.sid = r.id; this.baseline = JSON.stringify(this.form); try { localStorage.setItem("ljk_sid", this.sid); } catch {}
          await this.refresh();
        } catch (e) { this.toast(e.message, true); return; }
      }
      const ok = [...this.staged];
      this.uploading = true;
      this.up = { done: 0, total: ok.length }; this.upStatus = `Mengunggah ${ok.length} berkas…`;
      clientLog("upload_start", { n: ok.length });
      let i = 0, fail = 0;
      const worker = async () => {
        while (i < ok.length) {
          const it = ok[i++], fd = new FormData(); fd.append("files", it.file, it.file.name);
          try {
            await this.api(`/api/sessions/${this.sid}/files`, { method: "POST", body: fd });
            this.removeStagedQuiet(it.id);
          } catch (e) { fail++; this.toast(`${it.file.name}: ${e.message}`, true); }
          this.up.done++;
          if (this.up.done % 3 === 0 || this.up.done === ok.length) this.refresh();
        }
      };
      await Promise.all([worker(), worker(), worker()]);   // 3 unggahan paralel
      this.uploading = false;
      await this.refresh();
      this.upStatus = fail ? `${ok.length - fail} berkas terunggah, ${fail} gagal — yang gagal tetap di daftar, tekan Unggah untuk coba lagi.` : `${ok.length} berkas terunggah — sedang dipindai…`;
      clientLog("upload_done", { ok: ok.length - fail, fail });
      if (!fail) this.toast(`${ok.length} berkas diunggah`);
    },
    removeStagedQuiet(id) {
      const i = this.staged.findIndex(x => x.id === id); if (i < 0) return;
      if (this.staged[i].url) URL.revokeObjectURL(this.staged[i].url);
      this.staged.splice(i, 1);
    },
  };
}
