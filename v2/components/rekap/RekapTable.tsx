"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import Pager, { usePaged } from "@/components/Pager";
import SearchSelect from "@/components/SearchSelect";

export interface RekapView {
  submission_id: string;
  kelas_id: string;
  prodi: string | null;
  nim: string;
  nama: string | null;
  kode_soal: string | null;
  benar: number | null;
  salah: number | null;
  kosong: number | null;
  total: number | null;
  nilai: number | null;
  status: string;
}

export interface BelumView {
  kelas_id: string;
  prodi: string | null;
}

export interface RekapFilter {
  prodi: string;
  kelas: string;
  status: string;
}

type SortKey = "kelas_id" | "nim" | "nama" | "kode_soal" | "nilai";
type Sort = { key: SortKey; dir: "asc" | "desc" } | null;

const PAGE_DEFAULT = 20;

export default function RekapTable({ rows, belum, initial }: { rows: RekapView[]; belum: BelumView[]; initial: RekapFilter }) {
  const [f, setF] = useState<RekapFilter>(initial);
  const [sort, setSort] = useState<Sort>(null);

  // filter langsung diterapkan + disimpan di URL (bisa dibagikan / tahan reload)
  const apply = (next: Partial<RekapFilter>) => {
    const n = { ...f, ...next };
    // ganti prodi: kosongkan kelas bila kelas itu bukan milik prodi baru
    if (next.prodi !== undefined && n.kelas && n.prodi && !rows.some((r) => r.kelas_id === n.kelas && r.prodi === n.prodi)) n.kelas = "";
    setF(n);
    const qs = new URLSearchParams(Object.entries(n).filter(([, v]) => v)).toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  };

  const prodiOpts = useMemo(() => {
    const m = new Map<string, number>();
    rows.forEach((r) => r.prodi && m.set(r.prodi, (m.get(r.prodi) ?? 0) + 1));
    belum.forEach((b) => b.prodi && !m.has(b.prodi) && m.set(b.prodi, 0));
    return [...m].sort((a, b) => a[0].localeCompare(b[0])).map(([value, n]) => ({ value, hint: n }));
  }, [rows, belum]);

  const kelasOpts = useMemo(() => {
    const m = new Map<string, number>();
    rows.filter((r) => !f.prodi || r.prodi === f.prodi).forEach((r) => m.set(r.kelas_id, (m.get(r.kelas_id) ?? 0) + 1));
    return [...m].sort((a, b) => a[0].localeCompare(b[0])).map(([value, n]) => ({ value, hint: n }));
  }, [rows, f.prodi]);

  const shown = useMemo(() => {
    const out = rows.filter(
      (r) => (!f.prodi || r.prodi === f.prodi) && (!f.kelas || r.kelas_id === f.kelas) && (!f.status || r.status === f.status),
    );
    if (!sort) return out;
    const mul = sort.dir === "asc" ? 1 : -1;
    return [...out].sort((a, b) => {
      const x = a[sort.key];
      const y = b[sort.key];
      // nilai kosong selalu di bawah, apa pun arah urutannya
      if (x === null || x === undefined) return y === null || y === undefined ? 0 : 1;
      if (y === null || y === undefined) return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "id", { numeric: true });
      return c * mul || a.nim.localeCompare(b.nim);
    });
  }, [rows, f, sort]);

  const pg = usePaged(shown, "rekap-v2", `${f.prodi}|${f.kelas}|${f.status}|${sort?.key}|${sort?.dir}`, PAGE_DEFAULT);

  const nilai = shown.map((r) => r.nilai).filter((x): x is number => x !== null).map(Number);
  const avg = nilai.length ? nilai.reduce((a, b) => a + b, 0) / nilai.length : 0;
  const tanpaKunci = shown.filter((r) => r.nilai === null).length;
  const belumN = belum.filter((b) => (!f.prodi || b.prodi === f.prodi) && (!f.kelas || b.kelas_id === f.kelas)).length;

  // klik: nilai mulai dari tertinggi, kolom lain A→Z; klik ketiga kembali ke urutan awal
  const toggle = (key: SortKey) =>
    setSort((s) => {
      const first = key === "nilai" ? "desc" : "asc";
      if (!s || s.key !== key) return { key, dir: first };
      if (s.dir === first) return { key, dir: first === "asc" ? "desc" : "asc" };
      return null;
    });

  const Th = ({ k, children, right }: { k: SortKey; children: React.ReactNode; right?: boolean }) => {
    const on = sort?.key === k;
    return (
      <th className={`px-3 py-2 ${right ? "text-right" : ""}`} aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
        <button
          type="button"
          onClick={() => toggle(k)}
          className={`inline-flex items-center gap-1 uppercase hover:text-ink ${on ? "text-ink" : ""}`}
          title="Klik untuk mengurutkan"
        >
          {children}
          <span className={on ? "" : "text-ink3/60"}>{on ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}</span>
        </button>
      </th>
    );
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Box label="LJK" value={shown.length} />
        <Box label="Valid" value={shown.filter((r) => r.status === "valid").length} />
        <Box label="Rata-rata nilai" value={Math.round(avg * 10) / 10} />
        <Box label="Belum upload" value={belumN} />
      </div>
      {tanpaKunci > 0 && (
        <div className="rounded-lg bg-warn-bg p-3 text-sm text-warn">{tanpaKunci} LJK belum bisa dinilai (kode soal kosong atau kunci belum diisi).</div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <SearchSelect
          className="w-56"
          value={f.prodi}
          onChange={(v) => apply({ prodi: v })}
          options={prodiOpts}
          allLabel="Semua prodi"
          placeholder="Cari prodi…"
        />
        <SearchSelect
          className="w-64"
          value={f.kelas}
          onChange={(v) => apply({ kelas: v })}
          options={kelasOpts}
          allLabel={f.prodi ? `Semua kelas ${f.prodi}` : "Semua kelas"}
          placeholder="Cari kelas… (mis. IF 05)"
        />
        <select value={f.status} onChange={(e) => apply({ status: e.target.value })} className="input max-w-[12rem]">
          <option value="">Semua status</option>
          <option value="valid">Valid</option>
          <option value="perlu_validasi">Perlu validasi</option>
        </select>
        {(f.prodi || f.kelas || f.status || sort) && (
          <button
            type="button"
            className="text-xs font-semibold text-ink2 hover:text-ink hover:underline"
            onClick={() => {
              apply({ prodi: "", kelas: "", status: "" });
              setSort(null);
            }}
          >
            Reset
          </button>
        )}
      </div>

      <div className="space-y-3">
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-soft text-left text-xs text-muted uppercase">
              <tr>
                <Th k="kelas_id">Kelas</Th>
                <Th k="nim">NIM</Th>
                <Th k="nama">Nama</Th>
                <Th k="kode_soal">Kode</Th>
                <th className="px-3 py-2 text-right">B/S/K</th>
                <Th k="nilai" right>
                  Nilai
                </Th>
                <th className="px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {pg.slice.map((r) => (
                <tr key={r.submission_id} className="hover:bg-soft">
                  <td className="px-3 py-2 text-xs whitespace-nowrap">{r.kelas_id}</td>
                  <td className="px-3 py-2 font-mono text-xs">{r.nim}</td>
                  <td className="px-3 py-2">
                    <Link href={`/validasi/s/${r.submission_id}`} className="hover:underline">
                      {r.nama ?? <span className="text-warn">di luar daftar</span>}
                    </Link>
                  </td>
                  <td className="px-3 py-2 font-mono">{r.kode_soal ?? "—"}</td>
                  <td className="px-3 py-2 text-right text-xs whitespace-nowrap tabular-nums">
                    {r.total ? `${r.benar}/${r.salah}/${r.kosong}` : "—"}
                  </td>
                  <td className="px-3 py-2 text-right font-semibold tabular-nums">{r.nilai ?? "—"}</td>
                  <td className="px-3 py-2">
                    <span className={`pill ${r.status === "valid" ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}>
                      {r.status === "valid" ? "valid" : "menunggu"}
                    </span>
                  </td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-sm text-muted">
                    Tidak ada data.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pager p={pg} />
      </div>
    </div>
  );
}

function Box({ label, value }: { label: string; value: number }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-semibold text-muted uppercase">{label}</div>
      <div className="mt-1 text-2xl font-bold">{value.toLocaleString("id-ID")}</div>
    </div>
  );
}
