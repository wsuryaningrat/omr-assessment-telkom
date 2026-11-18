"use client";

import Link from "next/link";
import Pager, { usePaged } from "@/components/Pager";

export interface RekapView {
  submission_id: string;
  kelas_id: string;
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

export default function RekapTable({ rows, resetKey }: { rows: RekapView[]; resetKey: string }) {
  const pg = usePaged(rows, "rekap", resetKey);
  return (
    <div className="space-y-3">
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-soft text-left text-xs text-muted uppercase">
            <tr>
              <th className="px-3 py-2">Kelas</th>
              <th className="px-3 py-2">NIM</th>
              <th className="px-3 py-2">Nama</th>
              <th className="px-3 py-2">Kode</th>
              <th className="px-3 py-2 text-right">B/S/K</th>
              <th className="px-3 py-2 text-right">Nilai</th>
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
            {rows.length === 0 && (
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
  );
}
