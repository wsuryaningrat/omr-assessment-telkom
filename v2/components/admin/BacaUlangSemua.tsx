"use client";

import { useRef, useState } from "react";
import { asset } from "@/lib/basePath";

type Hasil = { id: string; kelas: string; nim: string; terisi: number; flagsLama: number; flags: number; valid: boolean; error?: string };

/** Panel admin: baca ulang semua LJK yang belum tervalidasi di server, per kelompok kecil, dengan progres. */
export default function BacaUlangSemua({ total }: { total: number }) {
  const [autoValid, setAutoValid] = useState(true);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(0);
  const [stat, setStat] = useState({ terisi: 0, bersih: 0, valid: 0, berkurang: 0 });
  const [gagal, setGagal] = useState<Hasil[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const stop = useRef(false);

  const mulai = async () => {
    if (!window.confirm(`Baca ulang ${total} LJK yang belum tervalidasi di server?${autoValid ? "\n\nLJK yang hasilnya bersih akan langsung ditandai valid." : ""}`)) return;
    stop.current = false;
    setBusy(true);
    setMsg(null);
    setDone(0);
    setGagal([]);
    const s = { terisi: 0, bersih: 0, valid: 0, berkurang: 0 };
    setStat({ ...s });
    let after: string | null = null;
    let n = 0;
    try {
      do {
        const res: Response = await fetch(asset("/api/baca-ulang"), {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ after, limit: 4, autoValid }),
        });
        const j = (await res.json()) as { results?: Hasil[]; next?: string | null; error?: string };
        if (!res.ok || !j.results) throw new Error(j.error ?? `HTTP ${res.status}`);
        for (const r of j.results) {
          n++;
          if (r.error) {
            setGagal((g) => [...g, r]);
            continue;
          }
          s.terisi += r.terisi;
          if (r.flags === 0) s.bersih++;
          if (r.valid) s.valid++;
          if (r.flags < r.flagsLama) s.berkurang++;
        }
        setDone(n);
        setStat({ ...s });
        after = j.next ?? null;
      } while (after && !stop.current);
      setMsg(stop.current ? "Dihentikan." : "Selesai.");
    } catch (e) {
      setMsg(`Berhenti karena error: ${(e as Error).message}. Jalankan lagi untuk melanjutkan.`);
    } finally {
      setBusy(false);
    }
  };

  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <section className="card space-y-3 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">Baca ulang LJK yang belum tervalidasi</h2>
          <p className="text-sm text-muted">
            Foto <b>{total}</b> LJK berstatus &ldquo;perlu validasi&rdquo; (semua kelas) dibaca ulang di server dengan pembaca OMR terbaru.
            Nomor yang dulu kosong/ragu diisi bila kini terbaca yakin; jawaban yang sudah terisi tidak diubah.
          </p>
        </div>
        {busy ? (
          <button type="button" className="btn-ghost px-4 py-2 text-sm" onClick={() => (stop.current = true)}>
            Hentikan
          </button>
        ) : (
          <button type="button" className="btn-primary px-4 py-2 text-sm disabled:opacity-50" disabled={total === 0} onClick={mulai}>
            ↻ Jalankan
          </button>
        )}
      </div>
      <label className="flex items-center gap-2 text-sm text-ink2">
        <input type="checkbox" checked={autoValid} disabled={busy} onChange={(e) => setAutoValid(e.target.checked)} />
        Langsung tandai <b>valid</b> bila tidak ada lagi nomor yang ragu dan NIM/kode soal cocok
      </label>
      {(busy || done > 0) && (
        <div className="space-y-1.5">
          <div className="h-2 overflow-hidden rounded-full bg-soft">
            <div className="h-full bg-brand transition-all" style={{ width: `${pct}%` }} />
          </div>
          <p className="text-xs text-ink2 tabular-nums">
            {done}/{total} dibaca · {stat.terisi} jawaban terisi · {stat.berkurang} LJK tandanya berkurang · {stat.bersih} LJK bersih
            {autoValid && ` (${stat.valid} ditandai valid)`}
            {gagal.length > 0 && <span className="text-bad"> · {gagal.length} gagal</span>}
            {msg && <b className="ml-1">{msg}</b>}
          </p>
        </div>
      )}
      {gagal.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer font-semibold text-bad">LJK yang gagal dibaca ulang ({gagal.length}) — cek manual</summary>
          <ul className="mt-2 space-y-1">
            {gagal.map((g) => (
              <li key={g.id}>
                <a className="text-brand hover:underline" href={asset(`/validasi/s/${g.id}`)}>
                  {g.kelas} · {g.nim}
                </a>{" "}
                <span className="text-muted">— {g.error}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
