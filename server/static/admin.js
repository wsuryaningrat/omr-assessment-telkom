function admin() {
  return {
    regradeKelas: "", regradeRes: null, token: "", usr: "", pwd: "", pwErr: "", pwBusy: false, authed: false, loginErr: "", ready: false, errMsg: "", me: { password: false, microsoft: false, google: false, authed: false, token_allowed: true }, tab: "monitoring", superuser: false, busy: false, kelas: "", mon: null, monDay: "", monFilter: "all", monQ: "", monBusy: false, monLuar: false,
    allTabs: [{ id: "monitoring", label: "Monitoring" }, { id: "ringkasan", label: "Ringkasan" }, { id: "sesi", label: "Sesi" }, { id: "kunci", label: "Kunci jawaban" }, { id: "kalibrasi", label: "Kalibrasi" }, { id: "akun", label: "Akun", superOnly: true }, { id: "ekspor", label: "Ekspor" }],
    get tabs() { return this.allTabs.filter(t => !t.superOnly || this.superuser); },
    sum: { state: {} }, kunci: [], ses: { items: [], total: 0, page: 1, size: 25, q: "", status: "all", hari: "", sort: "", dir: "desc" }, detail: null, preview: { url: "", loading: false, zoom: 1, panX: 0, panY: 0, panning: false },
    meta: {}, editForm: { npm: "", kode_soal: "", fakultas_ljk: "", jawaban: {}, kuisioner: {} },
    users: [], newUser: { username: "", password: "", name: "", hp: "" }, newEmail: { email: "", name: "" }, userBusy: false, accessLog: [],
    calib: { fields: [], canvas: null, maxOffset: 300, token: "", field: "", draftDx: 0, draftDy: 0, savedDx: 0, savedDy: 0, previewUrl: "", busy: false, uploading: false, _t: null },
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
      try { this.meta = await (await fetch("/api/meta")).json(); } catch {}
      this.ready = true;
    },
    get fakultasOptions() { return this.meta.fakultas || []; },
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
      try { this.sum = await this.json("/api/admin/summary"); try { this.superuser = !!(await this.json("/api/admin/whoami")).superuser; } catch { this.superuser = false; } this.authed = true; try { sessionStorage.setItem("adm_tok", this.token); } catch {} this.startPoll(); await this.loadMonitor(); }
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
    monLabel(st) { return { selesai: "Selesai", berjalan: "Checking", belum: "Belum" }[st] || st; },
    monWhen(iso) { if (!iso) return ""; try { return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); } catch { return ""; } },
    // Format ringkas satu baris utk kolom "Upload at" tabel Sesi, mis. "10-01: 20.00" (MM-DD: HH.MM).
    sesWhen(iso) {
      if (!iso) return "-";
      try { const d = new Date(iso), p = n => String(n).padStart(2, "0"); return `${p(d.getMonth() + 1)}-${p(d.getDate())}: ${p(d.getHours())}.${p(d.getMinutes())}`; }
      catch { return "-"; }
    },
    // Ringkas nama pengawas jadi 2 kata pertama di tabel Sesi (nama lengkap tetap ada di title/tooltip)
    // -- nama panjang (mis. gelar dosen) bikin baris melebar & bikin kolom lain tak sejajar.
    shortName(nama) { return (nama || "").trim().split(/\s+/).slice(0, 2).join(" "); },
    async go(t) { if (t === "akun" && !this.superuser) return; this.tab = t; if (t === "monitoring") await this.loadMonitor(); if (t === "sesi") await this.loadSessions(); if (t === "kunci") await this.loadKunci(); if (t === "ringkasan") await this.loadSummary(); if (t === "kalibrasi") await this.loadCalib(); if (t === "akun") await this.loadUsers(); },
    async goToSesi(kelas) { this.tab = "sesi"; this.ses.status = "all"; this.ses.q = kelas || ""; this.ses.page = 1; await this.loadSessions(); },
    async loadSummary() { try { this.sum = await this.json("/api/admin/summary"); } catch {} },
    async loadSessions() {
      const s = this.ses, p = new URLSearchParams({ page: s.page, size: s.size, q: s.q, status: s.status, hari: s.hari, sort: s.sort, dir: s.dir });
      try { const d = await this.json("/api/admin/sessions?" + p); Object.assign(this.ses, { items: d.items, total: d.total }); } catch (e) { this.toast(e.message, true); }
    },
    // Klik judul kolom tabel Sesi: urut naik, klik lagi turun, klik ketiga kembali ke urutan asli (terbaru).
    // Pengurutan dilakukan di server (lintas halaman), bukan hanya baris yg sedang tampil.
    sortSes(key) {
      const s = this.ses;
      if (s.sort !== key) { s.sort = key; s.dir = "asc"; }
      else if (s.dir === "asc") s.dir = "desc";
      else { s.sort = ""; s.dir = "desc"; }
      s.page = 1; this.loadSessions();
    },
    sortArrow(key) { return this.ses.sort === key ? (this.ses.dir === "asc" ? "▲" : "▼") : ""; },
    sesStatusLabel(st) { return { scanning: "Scanning", perlu_cek: "Checking", validated: "Validated" }[st] || st; },
    sesStatusChip(st) { return { scanning: "scanning", perlu_cek: "check", validated: "validated" }[st] || ""; },
    kirimLabel(i) { if (!i.submitted) return "Belum dikirim"; if (i.synced) return "Sent"; if (i.sync_error) return "Gagal ×" + i.sync_attempts; return "Antre"; },
    kirimChip(i) { if (!i.submitted) return "pending"; if (i.synced) return "validated"; if (i.sync_error) return "warning"; return "pending"; },
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
    async syncSessionNow(i) {
      if (!i.admin_validated) { this.toast("Tandai sesi ini validated dulu (tombol centang di sebelah kiri) sebelum Kirim", true); return; }
      if (!i.synced && !confirm("Kirim sesi ini ke Google Sheet? Sesi akan dikunci (submitted) lalu disinkronkan.")) return;
      i.syncing = true;
      try {
        const d = await this.json(`/api/admin/sessions/${i.id}/sync-now`, { method: "POST" });
        const parts = [];
        if (d.appended) parts.push(`${d.appended} baris baru`);
        if (d.updated) parts.push(`${d.updated} baris diperbarui`);
        this.toast(parts.length ? `Terkirim — ${parts.join(", ")} ✓` : "Tak ada lembar utk dikirim");
        await this.loadSessions();
      } catch (e) { this.toast(e.message, true); }
      i.syncing = false;
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
    async openDetail(i) {
      clearTimeout(this._autoT); this._dirtyId = null;
      this.detail = { id: i.id, nama: i.nama, kelas: i.kelas, prodi: i.prodi, admin_validated_by: i.admin_validated_by, admin_validated: !!i.admin_validated, sesBusy: false,
        items: [], orphans: [], loading: true, selectedId: null, selectedItem: null, previewMode: "original",
        questionNums: [], kuisionerNums: [], bulkKode: "", bulkFakultas: "", bulkBusy: false, saving: false, savedAt: 0, checkedIds: [] };
      try {
        const d = await this.json(`/api/admin/sessions/${i.id}/sheets`);
        this.detail.items = d.items; this.detail.orphans = d.orphan_files || [];
      } catch (e) { this.toast(e.message, true); this.detail = null; return; }
      this.detail.loading = false;
      if (this.detail.items.length) this.selectDetailSheet(this.detail.items[0]);
      else this._setPreview("");
    },
    sheetStatusIcon(label) { return { OK: "✓", Gagal: "⚠" }[label] || "⏳"; },
    async toggleSheetValidated(x) {
      const want = !x.validated;
      try {
        const d = await this.json(`/api/admin/sheets/${x.id}/validate?value=${want}`, { method: "POST" });
        x.validated = d.validated; x.label = d.label;
        if (this.detail?.selectedItem?.id === x.id) { this.detail.selectedItem.validated = d.validated; this.detail.selectedItem.label = d.label; }
      } catch (e) { this.toast(e.message, true); }
    },
    // Pilih satu lembar di kolom kiri: muat pratinjau foto asli + isi form edit (kolom kanan) dari record-nya.
    selectDetailSheet(x) {
      if (this.detail.selectedId !== x.id) this.flushAutoSave();
      this.detail.selectedId = x.id; this.detail.selectedItem = x;
      this._loadEditForm(x);
      this.detail.previewMode = "original";
      if (x.photo_exists) this.showDetailPreview("original"); else this._setPreview("");
    },
    _loadEditForm(x) {
      const rec = x.record || {};
      const qs = Object.keys(rec).filter(k => /^soal_\d{2}$/.test(k)).sort();
      this.detail.questionNums = qs.map(k => k.slice(5));
      const jawaban = {}; qs.forEach(k => { jawaban[k.slice(5)] = rec[k] || "BLANK"; });
      const ks = Object.keys(rec).filter(k => /^q\d{2}$/.test(k)).sort();
      this.detail.kuisionerNums = ks.map(k => k.slice(1));
      const kuisioner = {}; ks.forEach(k => { kuisioner[k.slice(1)] = rec[k] || "BLANK"; });
      // Fakultas hasil OCR kadang bukan salah satu pilihan valid (mis. "MULTIPLE" -- beberapa kotak
      // tercentang sekaligus): jangan taruh nilai itu di <select> (browser tak bisa mencocokkannya ke
      // opsi mana pun), biarkan kosong supaya admin memilih yg benar -- nilai aslinya tetap terlihat
      // sbg keterangan di bawah dropdown (lihat detail.selectedItem.fakultas_ljk di admin.html).
      const fakRaw = rec["Fakultas (LJK)"] || "";
      const fak = this.fakultasOptions.includes(fakRaw) ? fakRaw : "";
      this.editForm = { npm: rec["NPM"] || "", kode_soal: rec["Kode Soal"] || "", fakultas_ljk: fak, jawaban, kuisioner };
      // Alpine kadang gagal mensinkronkan <select :value> ke DOM saat elemen ini BARU dipasang di render
      // yg sama (mis. lewat x-if) -- opsinya (x-for) belum tentu selesai dipasang saat :value dievaluasi
      // pertama kali, jadi browser diam2 menolak nilai yg belum ada opsinya & Alpine tak pernah mencoba
      // lagi. Paksa sinkron manual sekali lagi setelah DOM benar2 selesai dirender (nextTick).
      this.$nextTick(() => { const el = document.getElementById("edFak"); if (el) el.value = fak; });
    },
    // "Hilang senyap": berkas tercatat selesai tapi nol lembar (mis. terputus restart server di tengah
    // pemindaian lembar sulit). Foto sumbernya aman -- diproses ulang lewat antrean latar belakang biasa.
    async reprocessOneFile(o) {
      try { await this.json(`/api/admin/files/${o.id}/reprocess`, { method: "POST" }); this.toast(`${o.name}: diantre utk dipindai ulang`); await this.openDetail(this.detail); }
      catch (e) { this.toast(e.message, true); }
    },
    async reprocessOrphans(i) {
      try { const d = await this.json(`/api/admin/sessions/${i.id}/reprocess-orphans`, { method: "POST" }); this.toast(`${d.diproses_ulang} berkas diantre utk dipindai ulang`); await this.loadSessions(); if (this.detail && this.detail.id === i.id) await this.openDetail(i); }
      catch (e) { this.toast(e.message, true); }
    },
    // Pindai ulang SEMUA lembar sesi (bukan cuma yg hilang senyap) -- mis. utk menyegarkan hasil lama
    // setelah perbaikan deteksi. Latar belakang & bisa makan waktu utk sesi besar; progres muncul di kolom Proses.
    async rescanAllSheets(i) {
      const n = i.lembar ?? (i.items ? i.items.length : "semua");
      if (!confirm(`Pindai ulang SEMUA ${n} lembar di sesi ini dgn foto sumber yg sama? Status "sudah dicek per-lembar" akan direset & nilai bisa berubah kalau hasil bacanya beda. Berjalan di latar belakang & bisa makan waktu -- sebaiknya di luar jam sibuk unggahan.`)) return;
      try {
        const d = await this.json(`/api/admin/sessions/${i.id}/rescan-all`, { method: "POST" });
        this.toast(`${d.diantre} lembar diantre utk dipindai ulang` + (d.dilewati ? ` (${d.dilewati} dilewati, foto tak ada)` : ""));
        await this.loadSessions();
      } catch (e) { this.toast(e.message, true); }
    },
    // Tandai / batalkan "validated" untuk SESI ini dari dalam popup Detail (sama dgn tombol centang di tabel
    // Sesi: memvalidasi semua lembar yg terbaca + menilai ulang; mengirim ke Sheet tetap lewat tombol Kirim).
    async toggleSessionValidated(d) {
      const want = !d.admin_validated;
      if (want && !confirm("Tandai sesi ini validated? Semua lembar yang berhasil terbaca akan divalidasi.")) return;
      d.sesBusy = true;
      try {
        const r = await this.json(`/api/admin/sessions/${d.id}/validate?value=${want}`, { method: "POST" });
        d.admin_validated = r.admin_validated; d.admin_validated_by = r.admin_validated_by;
        this.toast(want ? "Sesi divalidasi" : "Tanda validated dibatalkan");
        const keep = d.selectedId;
        await this.loadSessions();
        if (want) { await this.openDetail(d); const again = this.detail?.items.find(it => it.id === keep); if (again) this.selectDetailSheet(again); }
      } catch (e) { this.toast(e.message, true); }
      d.sesBusy = false;
    },
    async validateAllSheets(d) {
      if (!d.items.length) return;
      if (!confirm(`Validasi SEMUA ${d.items.length} lembar di sesi ini (lembar yg gagal terbaca dilewati)?`)) return;
      try {
        const r = await this.json(`/api/admin/sessions/${d.id}/validate-all-sheets`, { method: "POST" });
        this.toast(`${r.divalidasi} lembar divalidasi`);
        await this.openDetail(d);
      } catch (e) { this.toast(e.message, true); }
    },
    async unvalidateAllSheets(d) {
      if (!d.items.length) return;
      if (!confirm(`Batalkan validasi SEMUA ${d.items.length} lembar di sesi ini?`)) return;
      try {
        const r = await this.json(`/api/admin/sessions/${d.id}/unvalidate-all-sheets`, { method: "POST" });
        this.toast(`${r.dibatalkan} lembar dibatalkan validasinya`);
        await this.openDetail(d);
      } catch (e) { this.toast(e.message, true); }
    },
    toggleChecked(id) {
      const ids = this.detail.checkedIds;
      const i = ids.indexOf(id);
      if (i === -1) ids.push(id); else ids.splice(i, 1);
    },
    async rescanSelectedSheets(d) {
      const ids = d.checkedIds;
      if (!ids.length) return;
      if (!confirm(`Pindai ulang ${ids.length} lembar terpilih dgn foto sumber yg sama?`)) return;
      try {
        const r = await this.json(`/api/admin/sessions/${d.id}/rescan-selected`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sheet_ids: ids }) });
        this.toast(`${r.diantre} lembar diantre utk dipindai ulang` + (r.dilewati ? ` (${r.dilewati} dilewati, foto tak ada)` : ""));
        d.checkedIds = [];
        await this.openDetail(d);
      } catch (e) { this.toast(e.message, true); }
    },
    // Hapus SEKALIGUS lembar2 yg dicentang (checkbox per baris) -- sama spt deleteSheet() tapi utk
    // beberapa lembar dlm satu aksi (lihat POST /api/admin/sessions/{sid}/delete-selected).
    async deleteSelectedSheets(d) {
      const ids = d.checkedIds;
      if (!ids.length) return;
      if (!confirm(`Hapus ${ids.length} lembar terpilih dari sesi ini? Tindakan ini tidak bisa dibatalkan.`)) return;
      try {
        const r = await this.json(`/api/admin/sessions/${d.id}/delete-selected`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sheet_ids: ids }) });
        this.toast(`${r.dihapus} lembar dihapus`);
        d.checkedIds = [];
        await this.openDetail(d);
        await this.loadSessions();   // kolom "Lembar" di tabel Sesi ikut diperbarui
      } catch (e) { this.toast(e.message, true); }
    },
    // Ringkasan keterisian satu lembar utk ditampilkan di daftar lembar (kolom kiri popup Detail) --
    // dipakai jg utk tanda peringatan NPM ganjil/jawaban byk kosong, supaya admin tak perlu buka tiap
    // lembar satu2 utk tahu mana yg patut dicek lebih dulu.
    sheetFillInfo(x) {
      const rec = x.record || {};
      const npm = (rec["NPM"] || "").toString();
      const kui = Object.keys(rec).filter(k => /^q\d{2}$/.test(k));
      const soal = Object.keys(rec).filter(k => /^soal_\d{2}$/.test(k));
      const kuiFilled = kui.filter(k => rec[k] && rec[k] !== "BLANK").length;
      const soalFilled = soal.filter(k => rec[k] && rec[k] !== "BLANK").length;
      const total = kui.length + soal.length;
      const filled = kuiFilled + soalFilled;
      const rate = total ? filled / total : 1;
      // Bukan cuma panjang salah/kosong: hasil scan tiap digit NPM yg tak terbaca (blank) diisi SPASI,
      // bukan dibuang -- hanya spasi di UJUNG yg ikut hilang (lihat core/decoder.py decode_field,
      // "".join(digits).rstrip()). Jadi NPM bisa SAMA PANJANG 10 karakter tapi ada spasi/non-digit di
      // TENGAHNYA (mis. "103 125001") -- .length!==10 saja tak menangkap itu, perlu cek tiap karakter.
      const npmWarn = !/^\d{10}$/.test(npm);
      // Kuisioner dicek terpisah dgn AMBANG JUMLAH (bukan rasio) -- soal biasanya jauh lebih banyak drpd
      // kuisioner, jadi kuisioner kosong gampang "tenggelam" di rata-rata gabungan & lolos dari ambang
      // keterisian keseluruhan. kui.length===0 (sesi tanpa kuisioner sama sekali) sengaja TAK kena ini.
      const kuiWarn = kui.length > 0 && kuiFilled < 7;
      const warn = npmWarn || rate < 0.4 || kuiWarn;
      return { npm, kuiFilled, kuiTotal: kui.length, soalFilled, soalTotal: soal.length, warn, npmWarn, kuiWarn };
    },
    closeDetail() { this.flushAutoSave(); this._setPreview(""); this.detail = null; },
    _setPreview(url) { if (this.preview.url && this.preview.url.startsWith("blob:")) URL.revokeObjectURL(this.preview.url); this.preview = { url, loading: false, zoom: 1, panX: 0, panY: 0, panning: false }; },
    // Zoom/geser foto di kolom preview Detail sesi -- scroll utk zoom, seret utk geser saat diperbesar,
    // klik-2x/tombol utk reset. panX/panY dlm piksel LAYAR (dibagi zoom sebelum masuk transform, lihat
    // admin.html: transform CSS "scale() translate()" menerapkan translate DULU baru scale).
    zoomPreview(delta) {
      this.preview.zoom = Math.max(1, Math.min(4, +(this.preview.zoom + delta).toFixed(2)));
      if (this.preview.zoom <= 1) { this.preview.zoom = 1; this.preview.panX = 0; this.preview.panY = 0; }
    },
    resetZoom() { this.preview.zoom = 1; this.preview.panX = 0; this.preview.panY = 0; },
    // Klik gambar langsung zoom in/out (bukan dobel-klik lagi) -- tapi JANGAN ikut toggle zoom kalau klik
    // itu sebenarnya akhir dari seret/geser (lihat startPan: _dragged ditandai begitu gerak >3px).
    clickPreview() {
      if (this.preview._dragged) { this.preview._dragged = false; return; }
      this.preview.zoom > 1 ? this.resetZoom() : this.zoomPreview(1);
    },
    startPan(ev) {
      if (this.preview.zoom <= 1) return;
      ev.preventDefault();
      const startX = ev.clientX, startY = ev.clientY, origX = this.preview.panX, origY = this.preview.panY;
      this.preview.panning = true; this.preview._dragged = false;
      const move = (e) => {
        if (Math.abs(e.clientX - startX) > 3 || Math.abs(e.clientY - startY) > 3) this.preview._dragged = true;
        this.preview.panX = origX + (e.clientX - startX); this.preview.panY = origY + (e.clientY - startY);
      };
      const up = () => { this.preview.panning = false; window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
      window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
    },
    // Mode "scan" membuka gambar hasil scan yg SUDAH TERSIMPAN (instan, tanpa memindai ulang); refresh=true
    // memaksa gambarnya dibuat ulang dari foto sumber (hasil bacaan/nilai lembar tak berubah).
    async showDetailPreview(mode, refresh = false) {
      const x = this.detail?.selectedItem; if (!x || (mode === "scan" ? !(x.scan_cached || x.photo_exists) : !x.photo_exists)) return;
      this.detail.previewMode = mode;
      // Kedua mode lewat fetch+blob (bukan <img :src> langsung) supaya ADA status loading yg bisa
      // dikontrol (lihat .loadbar di atas popup) -- "Hasil scan" menjalankan pipeline OMR penuh & bisa
      // makan beberapa detik, jauh lebih lama dari "Foto asli" yg cuma decode gambar.
      this.preview.loading = true;
      try {
        const url = mode === "scan" ? `/api/sheets/${x.id}/preview${refresh ? "?refresh=1" : ""}` : `/api/admin/sheets/${x.id}/photo`;
        const r = await this.api(url);
        this._setPreview(URL.createObjectURL(await r.blob()));
        if (mode === "scan") x.scan_cached = true;
      } catch (e) { this.toast(e.message, true); this.preview.loading = false; }
    },
    async rescanSheet(x) {
      if (!x) return;
      x.busy = true;
      const keepId = x.id;
      try {
        const d = await this.json(`/api/admin/sheets/${x.id}/rescan`, { method: "POST" });
        this.toast("Dipindai ulang — status: " + d.label);
        await this.openDetail(this.detail);
        const again = this.detail?.items.find(it => it.id === keepId);
        if (again) this.selectDetailSheet(again);
      } catch (e) { this.toast(e.message, true); x.busy = false; }
      // `x.busy` SENGAJA tak direset di jalur sukses -- openDetail() di atas sudah mengganti detail.items
      // dgn objek baru (tanpa `busy`), jadi `x` lama ini dibuang begitu saja. Direset manual hanya di
      // jalur gagal supaya tombol tak macet abu-abu selamanya (bug lama: baris ini sebelumnya tak ada).
    },
    async replaceSheetPhoto(x, ev) {
      const f = ev.target.files && ev.target.files[0]; if (!f || !x) return;
      const fd = new FormData(); fd.append("file", f, f.name);
      const keepId = x.id;
      x.busy = true;
      try {
        const d = await this.json(`/api/admin/sheets/${x.id}/replace`, { method: "POST", body: fd });
        this.toast("Foto diganti & dipindai ulang — status: " + d.label);
        await this.openDetail(this.detail);
        const again = this.detail?.items.find(it => it.id === keepId);
        if (again) this.selectDetailSheet(again);
      } catch (e) { this.toast(e.message, true); x.busy = false; }
      ev.target.value = "";
    },
    // Hapus SATU lembar dari popup Detail (mis. salah foto/bukan LJK/duplikat halaman) -- lihat
    // DELETE /api/admin/sheets/{shid} (admin.py), beda dgn endpoint pengawas yg menolak sesi tersubmit.
    async deleteSheet(x) {
      if (!x) return;
      if (!confirm(`Hapus lembar #${x.seq} ini dari sesi? Tindakan ini tidak bisa dibatalkan.`)) return;
      try {
        await this.api(`/api/admin/sheets/${x.id}`, { method: "DELETE" });
        this.toast("Lembar dihapus");
        const wasSelected = this.detail.selectedId === x.id;
        this.detail.items = this.detail.items.filter(it => it.id !== x.id);
        this.detail.checkedIds = this.detail.checkedIds.filter(id => id !== x.id);
        if (wasSelected) {
          if (this.detail.items.length) this.selectDetailSheet(this.detail.items[0]);
          else { this.detail.selectedId = null; this.detail.selectedItem = null; this._setPreview(""); }
        }
        await this.loadSessions();   // kolom "Lembar" di tabel Sesi ikut diperbarui
      } catch (e) { this.toast(e.message, true); }
    },
    // NPM harus pas 10 digit -- merahin kotak input kalau kosong atau panjangnya salah (lihat jg
    // sheetFillInfo.npmWarn utk tanda yg sama di daftar lembar kolom kiri).
    get npmBad() { return (this.editForm.npm || "").replace(/\D/g, "").length !== 10; },
    // NPM ditempel dari clipboard sering 12 digit (mis. nomor KTM lengkap dgn prefiks tahun/fakultas) --
    // ambil 10 digit TERAKHIR otomatis saja drpd admin mengetik ulang/menghapus manual.
    onNpmPaste(ev) {
      ev.preventDefault();
      const text = (ev.clipboardData || window.clipboardData).getData("text");
      const digits = (text || "").replace(/\D/g, "");
      this.editForm.npm = digits.length > 10 ? digits.slice(-10) : digits;
      this.markDirty();
    },
    // Edit manual NPM/kode soal/fakultas/jawaban lembar yg sedang dipilih (kolom kanan Detail sesi).
    // Loncat ke kotak jawaban/kuisioner BERIKUTNYA begitu satu kotak terisi (spt input OTP) -- biar admin
    // bisa ketik beruntun tanpa klik tiap kotak. Tak loncat kalau kotak baru dikosongkan (backspace/hapus),
    // supaya koreksi satu huruf tak langsung terlempar ke kotak lain.
    advanceFocus(ev) {
      if (!ev.target.value) return;
      const next = ev.target.closest(".anscell")?.nextElementSibling?.querySelector("input");
      if (next) { next.focus(); next.select?.(); }
    },
    // Payload edit utk SATU lembar dari editForm saat ini. NPM yg masih diketik setengah jalan (bukan 10
    // digit, bukan kosong, & beda dari nilai tersimpan) SENGAJA tak dikirim -- server menolaknya (422) dan
    // autosave tak boleh memunculkan error di tiap ketukan; ia ikut terkirim begitu genap 10 digit.
    _editPayload(x) {
      const f = this.editForm, digits = (f.npm || "").replace(/\D/g, "");
      const orig = String((x.record || {})["NPM"] || "").replace(/\D/g, "");
      const body = { kode_soal: f.kode_soal, fakultas_ljk: f.fakultas_ljk, jawaban: f.jawaban, kuisioner: f.kuisioner };
      if (digits.length === 10 || digits === "" || digits === orig) body.npm = f.npm;
      return body;
    },
    // Kirim PATCH utk lembar x pada detail d (ditangkap eksplisit: autosave bisa berjalan setelah admin
    // pindah lembar / menutup popup). reload=true (tombol Simpan manual) memuat ulang form dari hasil server;
    // autosave TIDAK -- form yg sedang diketik tak boleh ditimpa di tengah jalan.
    async _persist(d, x, body, reload) {
      d.saving = true;
      try {
        const updated = await this.json(`/api/admin/sheets/${x.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const idx = d.items.findIndex(it => it.id === x.id);
        if (idx >= 0) d.items[idx] = updated;
        if (d.selectedId === x.id) {
          d.selectedItem = updated;
          if (reload) this._loadEditForm(updated);
        }
        d.savedAt = Date.now();
        if (reload) this.toast("Perubahan disimpan");
        return true;
      } catch (e) { this.toast(e.message, true); return false; }
      finally { d.saving = false; }
    },
    async saveDetailEdit() {
      const x = this.detail?.selectedItem; if (!x) return;
      clearTimeout(this._autoT); this._dirtyId = null;
      const body = { ...this._editPayload(x), npm: this.editForm.npm };
      await this._persist(this.detail, x, body, true);
    },
    // Autosave: tiap perubahan isian (NPM/kode soal/fakultas/jawaban/kuisioner) ditandai lewat markDirty()
    // lalu disimpan otomatis ~0,8 dtk setelah ketukan terakhir. Pindah lembar / tutup popup memaksa simpan
    // dulu (flushAutoSave) supaya ketikan terakhir tak hilang.
    markDirty() {
      if (!this.detail?.selectedItem) return;
      this._dirtyId = this.detail.selectedId;
      clearTimeout(this._autoT);
      this._autoT = setTimeout(() => this.autoSave(), 800);
    },
    async autoSave() {
      clearTimeout(this._autoT);
      const d = this.detail;
      if (!this._dirtyId || !d) return;
      if (d.saving) { this._autoT = setTimeout(() => this.autoSave(), 400); return; }
      const x = d.selectedItem;
      if (!x || x.id !== this._dirtyId) { this._dirtyId = null; return; }
      this._dirtyId = null;
      await this._persist(d, x, this._editPayload(x), false);
    },
    // Simpan seketika (tanpa menunggu debounce) bila ada perubahan tertunda -- dipanggil SEBELUM
    // editForm diganti (pindah lembar) atau detail dibuang (tutup popup). Tak di-await.
    flushAutoSave() {
      clearTimeout(this._autoT);
      const d = this.detail, x = d?.selectedItem;
      if (!this._dirtyId || !d || !x || x.id !== this._dirtyId) { this._dirtyId = null; return; }
      this._dirtyId = null;
      this._persist(d, x, this._editPayload(x), false);
    },
    // Terapkan Kode Soal dan/atau Fakultas ke SEMUA lembar sesi ini sekaligus (mis. satu blok terbaca
    // konsisten salah utk seluruh kelas) -- tak menyentuh NPM/jawaban (beda per mahasiswa).
    async applyBulkEdit() {
      const d0 = this.detail; if (!d0 || (!d0.bulkKode && !d0.bulkFakultas)) return;
      if (!confirm(`Terapkan ke SEMUA ${d0.items.length} lembar di sesi ini? Validasi per-lembar yg sudah ada akan direset.`)) return;
      d0.bulkBusy = true;
      try {
        const body = {}; if (d0.bulkKode) body.kode_soal = d0.bulkKode; if (d0.bulkFakultas) body.fakultas_ljk = d0.bulkFakultas;
        const d = await this.json(`/api/admin/sessions/${d0.id}/bulk-edit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        this.toast(`${d.diubah} lembar diperbarui`);
        await this.openDetail(d0);
      } catch (e) { this.toast(e.message, true); }
      if (this.detail) this.detail.bulkBusy = false;
    },
    async loadKunci() { try { this.kunci = await this.json("/api/admin/kunci"); } catch (e) { this.toast(e.message, true); } },
    // Kalibrasi posisi template: unggah 1 foto referensi (sekali, dicache di server), lalu geser tiap blok
    // & lihat pratinjau langsung tanpa unggah ulang. Lihat server/admin.py bag. "kalibrasi template".
    async loadCalib() {
      try {
        const d = await this.json("/api/admin/calib/fields");
        this.calib.fields = d.fields; this.calib.canvas = d.canvas; this.calib.maxOffset = d.max_offset;
        if (!this.calib.field && d.fields.length) this.selectCalibField(d.fields[0].name);
      } catch (e) { this.toast(e.message, true); }
    },
    selectCalibField(name) {
      const f = this.calib.fields.find(x => x.name === name);
      this.calib.field = name;
      this.calib.savedDx = f ? f.dx : 0; this.calib.savedDy = f ? f.dy : 0;
      this.calib.draftDx = this.calib.savedDx; this.calib.draftDy = this.calib.savedDy;
      this.refreshCalibPreview();
    },
    async uploadCalibPhoto(ev) {
      const f = ev.target.files && ev.target.files[0]; if (!f) return;
      this.calib.uploading = true;
      const fd = new FormData(); fd.append("file", f, f.name);
      try {
        const d = await this.json("/api/admin/calib/upload", { method: "POST", body: fd });
        this.calib.token = d.token;
        await this.refreshCalibPreview();
        this.toast("Foto referensi siap — pilih blok utk dikalibrasi");
      } catch (e) { this.toast(e.message, true); }
      this.calib.uploading = false;
      ev.target.value = "";
    },
    async refreshCalibPreview() {
      if (!this.calib.token) return;
      this.calib.busy = true;
      try {
        const q = new URLSearchParams({ token: this.calib.token, field: this.calib.field || "", dx: this.calib.draftDx, dy: this.calib.draftDy });
        const r = await this.api("/api/admin/calib/preview?" + q);
        const blob = await r.blob();
        if (this.calib.previewUrl) URL.revokeObjectURL(this.calib.previewUrl);
        this.calib.previewUrl = URL.createObjectURL(blob);
      } catch (e) { this.toast(e.message, true); }
      this.calib.busy = false;
    },
    scheduleCalibPreview() {
      const m = this.calib.maxOffset;
      this.calib.draftDx = Math.max(-m, Math.min(m, this.calib.draftDx || 0));
      this.calib.draftDy = Math.max(-m, Math.min(m, this.calib.draftDy || 0));
      clearTimeout(this.calib._t);
      this.calib._t = setTimeout(() => this.refreshCalibPreview(), 300);
    },
    nudgeCalib(dx, dy) {
      const m = this.calib.maxOffset;
      this.calib.draftDx = Math.max(-m, Math.min(m, +(this.calib.draftDx + dx).toFixed(1)));
      this.calib.draftDy = Math.max(-m, Math.min(m, +(this.calib.draftDy + dy).toFixed(1)));
      this.refreshCalibPreview();
    },
    async saveCalib() {
      try {
        await this.json(`/api/admin/calib/fields/${encodeURIComponent(this.calib.field)}`,
          { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dx: this.calib.draftDx, dy: this.calib.draftDy }) });
        this.toast("Kalibrasi disimpan — dipakai mulai pemindaian berikutnya");
        const sel = this.calib.field;
        await this.loadCalib();
        this.selectCalibField(sel);   // loadCalib() tak menyeleksi ulang field yg sama -- "savedDx/Dy" perlu disegarkan manual di sini
      } catch (e) { this.toast(e.message, true); }
    },
    async resetCalibField(name) {
      if (!confirm(`Kembalikan blok "${name}" ke posisi asli template?`)) return;
      try {
        await this.api(`/api/admin/calib/fields/${encodeURIComponent(name)}`, { method: "DELETE" });
        this.toast("Dikembalikan ke posisi asli");
        const wasSelected = this.calib.field === name;
        await this.loadCalib();
        if (wasSelected) this.selectCalibField(name);
      } catch (e) { this.toast(e.message, true); }
    },
    async resetCalibAll() {
      if (!confirm("Kembalikan SEMUA blok ke posisi asli template? Semua koreksi kalibrasi tersimpan akan dihapus.")) return;
      try {
        const d = await this.json("/api/admin/calib/reset", { method: "POST" });
        this.toast(`${d.direset} blok dikembalikan ke posisi asli`);
        const sel = this.calib.field;
        await this.loadCalib();
        if (sel) this.selectCalibField(sel);
      } catch (e) { this.toast(e.message, true); }
    },
    async act(path, okMsg) {
      this.busy = true;
      try { const d = await this.json(path, { method: "POST" }); this.toast(okMsg + (d && d.synced !== undefined ? ` (${d.synced} sesi terkirim)` : "")); await this.loadSummary(); }
      catch (e) { this.toast(e.message, true); }
      this.busy = false;
    },
    async migrateStorage() {
      this.busy = true;
      try {
        const d = await this.json("/api/admin/migrate-storage", { method: "POST" });
        this.toast(d.dipindah ? `${d.dipindah} berkas dipindah ke folder Fakultas/Prodi/Kelas (${d.sudah_benar} sudah benar)` : `Semua berkas sudah tertata (${d.sudah_benar} diperiksa)`,
          d.gagal > 0);
        if (d.gagal) this.toast(`${d.gagal} berkas gagal dipindah — cek log server`, true);
      } catch (e) { this.toast(e.message, true); }
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
    // Akun admin (tab Akun) -- akun yg dikelola di sini aktif seketika, beda dgn ADMIN_USER/ADMIN_ACCOUNTS
    // di deploy/.env yg butuh redeploy & sengaja tak ditampilkan/dikelola dari UI ini.
    async loadUsers() {
      try { this.users = await this.json("/api/admin/users"); } catch (e) { this.toast(e.message, true); }
      try { this.accessLog = await this.json("/api/admin/access-log"); } catch { /* riwayat akses opsional, jangan ganggu tab kalau gagal */ }
    },
    async createEmailUser() {
      const f = this.newEmail;
      if (!f.email) return;
      this.userBusy = true;
      try {
        await this.json("/api/admin/users/email", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
        this.toast(`Email "${f.email.toLowerCase()}" boleh masuk admin`);
        this.newEmail = { email: "", name: "" };
        await this.loadUsers();
      } catch (e) { this.toast(e.message, true); }
      this.userBusy = false;
    },
    async createUser() {
      const f = this.newUser;
      if (!f.username || !f.password) { this.toast("Isi username & password", true); return; }
      if (f.password.length < 8) { this.toast("Password minimal 8 karakter", true); return; }
      this.userBusy = true;
      try {
        await this.json("/api/admin/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
        this.toast(`Akun "${f.username}" dibuat`);
        this.newUser = { username: "", password: "", name: "", hp: "" };
        await this.loadUsers();
      } catch (e) { this.toast(e.message, true); }
      this.userBusy = false;
    },
    async toggleUserActive(u) {
      try { await this.json(`/api/admin/users/${u.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: !u.active }) }); this.toast(u.active ? "Akun dinonaktifkan" : "Akun diaktifkan lagi"); await this.loadUsers(); }
      catch (e) { this.toast(e.message, true); }
    },
    async resetUserPassword(u) {
      const pw = prompt(`Password baru utk "${u.username}" (min. 8 karakter):`);
      if (!pw) return;
      if (pw.length < 8) { this.toast("Password minimal 8 karakter", true); return; }
      try { await this.json(`/api/admin/users/${u.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pw }) }); this.toast("Password direset"); }
      catch (e) { this.toast(e.message, true); }
    },
    async deleteUser(u) {
      if (!confirm(`Hapus akun "${u.username}" permanen?`)) return;
      try { await this.api(`/api/admin/users/${u.id}`, { method: "DELETE" }); this.toast("Akun dihapus"); await this.loadUsers(); }
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
