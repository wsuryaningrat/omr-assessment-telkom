"use client";

import { useMemo, useState } from "react";
import { setPengawas } from "@/app/(app)/admin/actions";
import Pager, { usePaged } from "@/components/Pager";

export interface PengawasRow {
  email: string;
  nama: string;
  role: string;
  aktif: boolean;
  upload: number;
}

export default function PengawasTable({ rows, me }: { rows: PengawasRow[]; me: string }) {
  const [q, setQ] = useState("");
  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? rows.filter((p) => `${p.nama} ${p.email}`.toLowerCase().includes(s)) : rows;
  }, [rows, q]);
  const pg = usePaged(list, "admin-pengawas", q);
  return (
    <div className="mt-4 space-y-3">
      <input className="input sm:max-w-72" placeholder="Cari nama / email…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted uppercase">
            <tr>
              <th className="py-2">Nama</th>
              <th className="py-2">Email</th>
              <th className="py-2">Peran</th>
              <th className="py-2 text-right">Upload</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {pg.slice.map((p) => (
              <tr key={p.email} className={p.aktif ? "" : "opacity-50"}>
                <td className="py-2 pr-2">{p.nama}</td>
                <td className="py-2 pr-2 font-mono text-xs">{p.email}</td>
                <td className="py-2 pr-2">
                  <span className={`pill ${p.role === "admin" ? "bg-brand-soft text-brand" : "bg-soft text-ink2"}`}>{p.role}</span>
                  {!p.aktif && <span className="ml-1 pill bg-slate-200 text-ink2">nonaktif</span>}
                </td>
                <td className="py-2 pr-2 text-right tabular-nums">{p.upload}</td>
                <td className="py-2 text-right whitespace-nowrap">
                  {p.email !== me && (
                    <form action={setPengawas} className="inline-flex gap-1">
                      <input type="hidden" name="email" value={p.email} />
                      <input type="hidden" name="aktif" value={String(p.aktif)} />
                      <button name="action" value="toggle" className="btn-ghost px-2 py-1 text-xs">
                        {p.aktif ? "Nonaktifkan" : "Aktifkan"}
                      </button>
                      <button name="action" value={p.role === "admin" ? "demote" : "promote"} className="btn-ghost px-2 py-1 text-xs">
                        {p.role === "admin" ? "Jadikan pengawas" : "Jadikan admin"}
                      </button>
                    </form>
                  )}
                </td>
              </tr>
            ))}
            {list.length === 0 && (
              <tr>
                <td colSpan={5} className="py-6 text-center text-sm text-muted">
                  Tidak ada.
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
