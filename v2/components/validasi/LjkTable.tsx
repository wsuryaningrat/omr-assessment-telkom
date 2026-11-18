"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import Pager, { usePaged } from "@/components/Pager";
import { STATUS_LABEL, type SubmissionStatus } from "@/lib/types";

export interface LjkRow {
  id: string;
  nim: string;
  nama: string | null;
  kode_soal: string | null;
  flags: number;
  status: SubmissionStatus;
  uploaded_by: string;
  uploaded_at: string;
  validated_by: string | null;
}

/** Daftar LJK satu kelas (per tab status), dengan pencarian & paginasi. */
export default function LjkTable({ rows, tab }: { rows: LjkRow[]; tab: string }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? rows.filter((r) => r.nim.includes(s) || (r.nama ?? "").toLowerCase().includes(s)) : rows;
  }, [rows, q]);
  const pg = usePaged(list, "validasi-ljk", `${q}|${tab}`);
  const href = (id: string) => `/validasi/s/${id}?tab=${tab}`;

  return (
    <div className="space-y-3">
      {rows.length > 8 && <input className="input sm:max-w-72" placeholder="Cari nama / NIM…" value={q} onChange={(e) => setQ(e.target.value)} />}
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-soft text-left text-xs text-muted uppercase">
            <tr>
              <th className="px-3 py-2">Mahasiswa</th>
              <th className="hidden px-3 py-2 sm:table-cell">Kode</th>
              <th className="px-3 py-2">Tanda</th>
              <th className="hidden px-3 py-2 md:table-cell">Diupload</th>
              <th className="px-3 py-2">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {pg.slice.map((s) => (
              <tr
                key={s.id}
                className="cursor-pointer hover:bg-soft"
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest("a,button")) return;
                  router.push(href(s.id));
                }}
              >
                <td className="px-3 py-2">
                  <Link href={href(s.id)} className="block">
                    <div className="font-medium">{s.nama ?? <span className="text-warn">di luar daftar</span>}</div>
                    <div className="font-mono text-xs text-muted">{s.nim}</div>
                  </Link>
                </td>
                <td className="hidden px-3 py-2 font-mono sm:table-cell">{s.kode_soal ?? "—"}</td>
                <td className="px-3 py-2">
                  {s.flags > 0 ? <span className="pill bg-red-100 text-bad">{s.flags}</span> : <span className="text-xs text-muted">—</span>}
                </td>
                <td className="hidden px-3 py-2 text-xs text-muted md:table-cell">
                  {s.uploaded_by}
                  <br />
                  {new Date(s.uploaded_at).toLocaleString("id-ID", { dateStyle: "short", timeStyle: "short" })}
                </td>
                <td className="px-3 py-2">
                  <Link href={href(s.id)} className="text-xs font-semibold text-brand">
                    {STATUS_LABEL[s.status]}
                    {s.validated_by ? ` · ${s.validated_by.split("@")[0]}` : ""} →
                  </Link>
                </td>
              </tr>
            ))}
            {list.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-sm text-muted">
                  Kosong.
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
