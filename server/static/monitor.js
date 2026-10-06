function monitor() {
  return {
    items: [], q: "", status: "all", sortKey: "", sortDir: "desc", loading: true, lastLoad: "", _poll: null,
    kelas_upload: 0, kelas_total: 0, upload_pct: 0,
    page: 1, pageSize: 10, pageSizeOptions: [10, 20, 30, 50],
    msg: { text: "", bad: false, show: false }, _t: null,

    async init() {
      await this.load();
      this._poll = setInterval(() => this.load(), 5000);
    },
    async load() {
      try {
        const r = await fetch("/api/monitor-sesi");
        const d = await r.json();
        this.items = d.items || [];
        this.kelas_upload = d.kelas_upload || 0; this.kelas_total = d.kelas_total || 0; this.upload_pct = d.upload_pct || 0;
        this.lastLoad = new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      } catch {}
      this.loading = false;
    },
    get filtered() {
      let l = this.items;
      if (this.status !== "all") l = l.filter(i => i.status === this.status);
      const q = this.q.toLowerCase();
      if (q) l = l.filter(i => `${i.nama} ${i.kelas} ${i.prodi} ${i.kode_soal}`.toLowerCase().includes(q));
      if (this.sortKey) {
        const k = this.sortKey, asc = this.sortDir === "asc", order = { scanning: 0, perlu_cek: 1, validated: 2 };
        const val = i => k === "status" ? (order[i.status] ?? 9) : (typeof i[k] === "string" ? i[k].toLowerCase() : i[k]);
        const filled = l.filter(i => val(i) !== null && val(i) !== undefined && val(i) !== ""), empty = l.filter(i => !filled.includes(i));
        filled.sort((a, b) => (val(a) < val(b) ? -1 : val(a) > val(b) ? 1 : 0) * (asc ? 1 : -1));
        l = filled.concat(empty);
      }
      return l;
    },
    get pageCount() { return Math.max(1, Math.ceil(this.filtered.length / this.pageSize)); },
    get paged() {
      const p = Math.min(this.page, this.pageCount), start = (p - 1) * this.pageSize;
      return this.filtered.slice(start, start + this.pageSize);
    },
    // Klik judul kolom: naik -> turun -> kembali ke urutan asli (terbaru dulu).
    sortBy(key) {
      if (this.sortKey !== key) { this.sortKey = key; this.sortDir = "asc"; }
      else if (this.sortDir === "asc") this.sortDir = "desc";
      else { this.sortKey = ""; this.sortDir = "desc"; }
      this.page = 1;
    },
    sortArrow(key) { return this.sortKey === key ? (this.sortDir === "asc" ? "▲" : "▼") : ""; },
    statusLabel(st) { return { scanning: "Scanning", perlu_cek: "Checking", validated: "Validated" }[st] || st; },
    statusChip(st) { return { scanning: "scanning", perlu_cek: "check", validated: "validated" }[st] || ""; },
    hariLabel(v) { if (!v) return "-"; const d = new Date(v + "T00:00:00"); return isNaN(d) ? v : d.toLocaleDateString("id-ID", { weekday: "short", day: "2-digit", month: "short" }); },
    whenLabel(iso) { if (!iso) return "-"; try { return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); } catch { return "-"; } },
    toast(text, bad = false) { this.msg = { text, bad, show: true }; clearTimeout(this._t); this._t = setTimeout(() => (this.msg.show = false), bad ? 3500 : 2200); },
    // Klik nilai kelas di tabel -> salin ke clipboard.
    async copyKelas(val) {
      if (!val || val === "-") return;
      try { await navigator.clipboard.writeText(val); this.toast(`Kelas "${val}" disalin`); }
      catch { this.toast("Gagal menyalin ke clipboard", true); }
    },
  };
}
