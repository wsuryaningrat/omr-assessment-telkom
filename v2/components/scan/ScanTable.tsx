"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import Pager, { usePaged } from "@/components/Pager";
import { batalkanValidasi, hapusSesi, kirimSheet, validasiSemua } from "@/app/(app)/scan/actions";
import type { Progres } from "@/lib/types";
import { HARI_ORDER } from "@/lib/types";

type Act = "validasi" | "batal" | "kirim" | "hapus";

const ICON: Record<Act | "buka", { icon: string; label: string; cls: string }> = {
  buka: { icon: "☰", label: "Buka daftar LJK kelas ini untuk validasi", cls: "bg-ink text-white hover:bg-ink2" },
  validasi: { icon: "✓", label: "Validasi semua LJK yang menunggu", cls: "bg-ok-bg text-ok hover:bg-ok hover:text-white" },
  batal: { icon: "↶", label: "Batalkan validasi semua", cls: "bg-warn-bg text-warn hover:bg-warn hover:text-white" },
  kirim: { icon: "⇪", label: "Kirim LJK tervalidasi ke Google Sheet", cls: "bg-info-bg text-info hover:bg-info hover:text-white" },
  hapus: { icon: "🗑", label: "Hapus sesi: semua LJK & foto kelas ini", cls: "bg-bad-bg text-bad hover:bg-bad hover:text-white" },
};

const CONFIRM: Record<Act, (r: Progres) => string> = {
  validasi: (r) => `Tandai VALID semua ${r.perlu_validasi} LJK yang menunggu di ${r.kelas_id}?`,
  batal: (r) => `Kembalikan ${r.tervalidasi} LJK valid di ${r.kelas_id} ke "perlu validasi"?`,
  kirim: (r) => `Kirim ${r.tervalidasi} LJK tervalidasi ${r.kelas_id} ke Google Sheet?`,
  hapus: (r) => `HAPUS semua ${r.terupload} LJK beserta fotonya di ${r.kelas_id}? Tindakan ini tidak bisa dibatalkan.`,
};

/** Filter status kelas (digabung dari menu Validasi lama). */
export const KELAS_FILTER = {
  semua: { label: "Semua", test: () => true },
  perlu: { label: "Perlu validasi", test: (r: Progres) => r.perlu_validasi > 0 },
  ditandai: { label: "Ditandai", test: (r: Progres) => r.perlu_cek > 0 },
  belum: { label: "Belum mulai", test: (r: Progres) => r.terupload === 0 },
  selesai: { label: "Tervalidasi semua", test: (r: Progres) => r.terupload > 0 && r.perlu_validasi === 0 },
} as const;
export type KelasFilter = keyof typeof KELAS_FILTER;

export default function ScanTable({ rows, isAdmin, initialFilter = "semua" }: { rows: Progres[]; isAdmin: boolean; initialFilter?: KelasFilter }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [f, setF] = useState<KelasFilter>(initialFilter);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null);
  const [, start] = useTransition();

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return rows
      .filter((r) => KELAS_FILTER[f].test(r) && (!s || `${r.kelas_id} ${r.prodi} ${r.ruangan} ${r.hari}`.toLowerCase().includes(s)))
      .sort(
        (a, b) =>
          // filter validasi: yang paling banyak ditandai / menunggu di atas
          (f === "perlu" || f === "ditandai" ? b.perlu_cek - a.perlu_cek || b.perlu_validasi - a.perlu_validasi : 0) ||
          HARI_ORDER.indexOf(a.hari ?? "") - HARI_ORDER.indexOf(b.hari ?? "") ||
          (a.waktu ?? "").localeCompare(b.waktu ?? "") ||
          a.kelas_id.localeCompare(b.kelas_id),
      );
  }, [rows, q, f]);
  const counts = useMemo(
    () => Object.fromEntries(Object.entries(KELAS_FILTER).map(([k, v]) => [k, rows.filter((r) => v.test(r)).length])) as Record<KelasFilter, number>,
    [rows],
  );
  const pg = usePaged(list, "scan-kelas", `${q}|${f}`);

  const pickFilter = (k: KelasFilter) => {
    setF(k);
    // simpan di URL supaya tombol kembali browser mengingat filter
    window.history.replaceState(null, "", k === "semua" ? window.location.pathname : `?status=${k}`);
  };

  const run = (act: Act, r: Progres) => {
    if (!window.confirm(CONFIRM[act](r))) return;
    const fn = { validasi: validasiSemua, batal: batalkanValidasi, kirim: kirimSheet, hapus: hapusSesi }[act];
    setBusy(`${act}:${r.kelas_id}`);
    start(async () => {
      const res = await fn(r.kelas_id);
      setToast(res);
      setBusy(null);
    });
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="-mx-1 flex max-w-full gap-1.5 overflow-x-auto px-1 pb-0.5 text-xs font-semibold">
          {(Object.keys(KELAS_FILTER) as KelasFilter[]).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => pickFilter(k)}
              className={`pill shrink-0 px-3 py-1.5 ${f === k ? "bg-ink text-white" : "bg-white text-ink2 ring-1 ring-line hover:ring-ink3"}`}
            >
              {KELAS_FILTER[k].label}
              <span className={`ml-1.5 tabular-nums ${f === k ? "text-white/70" : "text-ink3"}`}>{counts[k]}</span>
            </button>
          ))}
        </div>
        <input className="input sm:ml-auto sm:max-w-72" placeholder="Cari kode kelas, prodi, ruangan, hari…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {toast && (
        <div className={`flex items-start justify-between gap-3 rounded-xl px-4 py-3 text-sm ${toast.ok ? "bg-ok-bg text-ok" : "bg-bad-bg text-bad"}`}>
          <span>{toast.msg}</span>
          <button onClick={() => setToast(null)} className="font-bold">
            ✕
          </button>
        </div>
      )}
      <div className="card overflow-x-auto">
        <table className="w-full sm:min-w-[720px] text-sm">
          <thead className="bg-soft text-left text-[11px] font-semibold tracking-wide text-ink3 uppercase">
            <tr>
              <th className="px-3 py-2.5">Kelas</th>
              <th className="hidden px-3 py-2.5 sm:table-cell">Jadwal</th>
              <th className="px-3 py-2.5">Progres</th>
              <th className="hidden px-3 py-2.5 md:table-cell">Status</th>
              <th className="px-3 py-2.5 text-right">Aksi</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {pg.slice.map((r) => {
              const pct = r.total_peserta ? Math.round((r.terupload / r.total_peserta) * 100) : 0;
              const vpct = r.total_peserta ? Math.round((r.tervalidasi / r.total_peserta) * 100) : 0;
              return (
                <tr
                  key={r.kelas_id}
                  className="cursor-pointer hover:bg-soft/60"
                  title={`Scan LJK ${r.kelas_id}`}
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest("a,button,[aria-disabled]")) return;
                    router.push(`/scan/${encodeURIComponent(r.kelas_id)}`);
                  }}
                >
                  <td className="px-2 py-2 sm:px-3 sm:py-2.5">
                    <Link href={`/scan/${encodeURIComponent(r.kelas_id)}`} className="font-semibold hover:text-brand">
                      {r.kelas_id}
                    </Link>
                    <div className="text-xs text-ink3">
                      {r.prodi}
                      <span className="sm:hidden"> · {r.hari} {r.waktu}</span>
                    </div>
                  </td>
                  <td className="hidden px-3 py-2.5 text-xs text-ink2 sm:table-cell">
                    <div className="font-semibold text-ink">{r.hari}</div>
                    {r.waktu} · {r.ruangan}
                  </td>
                  <td className="px-2 py-2 sm:w-40 sm:px-3 sm:py-2.5">
                    <div className="mb-1 text-xs tabular-nums">
                      <b>{r.terupload}</b>
                      <span className="text-ink3">/{r.total_peserta}</span>
                    </div>
                    <div className="relative h-1.5 overflow-hidden rounded-full bg-soft">
                      <div className="absolute inset-y-0 left-0 bg-warn/60" style={{ width: `${pct}%` }} />
                      <div className="absolute inset-y-0 left-0 bg-ok" style={{ width: `${vpct}%` }} />
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-1 md:hidden">
                      {r.terupload === 0 && <span className="pill bg-soft text-ink3">belum mulai</span>}
                      {r.perlu_cek > 0 && <span className="pill bg-bad-bg text-bad">{r.perlu_cek} ditandai</span>}
                      {r.perlu_validasi > 0 && <span className="pill bg-warn-bg text-warn">{r.perlu_validasi} perlu validasi</span>}
                      {r.tervalidasi > 0 && <span className="pill bg-ok-bg text-ok">{r.tervalidasi} valid</span>}
                      {r.terkirim_at && (
                        <span className="pill bg-info-bg text-info" title={new Date(r.terkirim_at).toLocaleString("id-ID")}>
                          terkirim {r.terkirim_jumlah}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="hidden px-3 py-2.5 md:table-cell">
                    <div className="flex flex-wrap gap-1">
                      {r.terupload === 0 && <span className="pill bg-soft text-ink3">belum mulai</span>}
                      {r.perlu_cek > 0 && <span className="pill bg-bad-bg text-bad">{r.perlu_cek} ditandai</span>}
                      {r.perlu_validasi > 0 && <span className="pill bg-warn-bg text-warn">{r.perlu_validasi} perlu validasi</span>}
                      {r.tervalidasi > 0 && <span className="pill bg-ok-bg text-ok">{r.tervalidasi} valid</span>}
                      {r.terkirim_at && (
                        <span className="pill bg-info-bg text-info" title={new Date(r.terkirim_at).toLocaleString("id-ID")}>
                          terkirim {r.terkirim_jumlah}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-2 py-2 sm:px-3 sm:py-2.5">
                    <div className="ml-auto flex w-[68px] flex-wrap justify-end gap-1 sm:w-auto sm:flex-nowrap">
                      <IconLink href={`/validasi/${encodeURIComponent(r.kelas_id)}`} a="buka" disabled={r.terupload === 0} />
                      <IconBtn a="validasi" disabled={r.perlu_validasi === 0} busy={busy === `validasi:${r.kelas_id}`} onClick={() => run("validasi", r)} />
                      <IconBtn a="batal" disabled={r.tervalidasi === 0} busy={busy === `batal:${r.kelas_id}`} onClick={() => run("batal", r)} />
                      {isAdmin && <IconBtn a="kirim" disabled={r.tervalidasi === 0} busy={busy === `kirim:${r.kelas_id}`} onClick={() => run("kirim", r)} />}
                      {isAdmin && <IconBtn a="hapus" disabled={r.terupload === 0} busy={busy === `hapus:${r.kelas_id}`} onClick={() => run("hapus", r)} />}
                    </div>
                  </td>
                </tr>
              );
            })}
            {list.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-muted">
                  Tidak ada kelas yang cocok.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <Pager p={pg} />
      <p className="text-xs text-ink3">
        Ketuk baris kelas untuk scan · ☰ daftar LJK &amp; validasi per LJK · ✓ validasi semua · ↶ batalkan validasi
        {isAdmin && " · ⇪ kirim ke Google Sheet · 🗑 hapus sesi"}
      </p>
    </div>
  );
}

function IconLink({ href, a, disabled }: { href: string; a: "buka"; disabled?: boolean }) {
  const c = ICON[a];
  const cls = `flex h-7 w-7 items-center sm:h-8 sm:w-8 justify-center rounded-lg text-sm font-bold transition ${c.cls}`;
  if (disabled)
    return (
      <span title="Belum ada LJK" aria-disabled className={`${cls} cursor-not-allowed opacity-30`}>
        {c.icon}
      </span>
    );
  return (
    <Link href={href} title={c.label} aria-label={c.label} className={cls}>
      {c.icon}
    </Link>
  );
}

function IconBtn({ a, onClick, disabled, busy }: { a: Act; onClick: () => void; disabled?: boolean; busy?: boolean }) {
  const c = ICON[a];
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      title={c.label}
      aria-label={c.label}
      className={`flex h-7 w-7 items-center sm:h-8 sm:w-8 justify-center rounded-lg text-sm font-bold transition disabled:cursor-not-allowed disabled:opacity-30 ${c.cls}`}
    >
      {busy ? <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" /> : c.icon}
    </button>
  );
}
