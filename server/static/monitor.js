function monitor() {
  return {
    items: [], q: "", status: "all", loading: true, lastLoad: "", _poll: null,
    mhs_upload: 0, mhs_total: 0, upload_pct: 0,

    async init() {
      await this.load();
      this._poll = setInterval(() => this.load(), 5000);
    },
    async load() {
      try {
        const r = await fetch("/api/monitor-sesi");
        const d = await r.json();
        this.items = d.items || [];
        this.mhs_upload = d.mhs_upload || 0; this.mhs_total = d.mhs_total || 0; this.upload_pct = d.upload_pct || 0;
        this.lastLoad = new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      } catch {}
      this.loading = false;
    },
    get filtered() {
      let l = this.items;
      if (this.status !== "all") l = l.filter(i => i.status === this.status);
      const q = this.q.toLowerCase();
      if (q) l = l.filter(i => `${i.nama} ${i.kelas} ${i.prodi} ${i.kode_soal}`.toLowerCase().includes(q));
      return l;
    },
    statusLabel(st) { return { scanning: "Scanning", perlu_cek: "Checking", validated: "Validated" }[st] || st; },
    statusChip(st) { return { scanning: "scanning", perlu_cek: "check", validated: "validated" }[st] || ""; },
    hariLabel(v) { if (!v) return "-"; const d = new Date(v + "T00:00:00"); return isNaN(d) ? v : d.toLocaleDateString("id-ID", { weekday: "short", day: "2-digit", month: "short" }); },
    whenLabel(iso) { if (!iso) return "-"; try { return new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); } catch { return "-"; } },
    stripKode(k) { const m = /^k[j]?(\d+)$/i.exec(k || ""); return m ? m[1] : (k || "-"); },
  };
}
