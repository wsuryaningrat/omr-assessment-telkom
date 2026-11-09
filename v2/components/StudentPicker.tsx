"use client";

import { useMemo, useState } from "react";
import type { Peserta } from "@/lib/types";

/** Dropdown cari mahasiswa dari daftar kelas (dengan saran NIM terdekat di atas). */
export default function StudentPicker({
  peserta,
  value,
  suggestions = [],
  uploaded,
  onChange,
  allowManual = true,
}: {
  peserta: Peserta[];
  value: string;
  suggestions?: string[];
  uploaded?: Set<string>;
  onChange: (nim: string, source: "dropdown" | "manual") => void;
  allowManual?: boolean;
}) {
  const [q, setQ] = useState("");
  const [manual, setManual] = useState(false);
  const [manualNim, setManualNim] = useState("");
  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    const filtered = peserta.filter((p) => !s || p.nim.includes(s) || p.nama.toLowerCase().includes(s));
    const sug = new Set(suggestions);
    return [...filtered].sort((a, b) => Number(sug.has(b.nim)) - Number(sug.has(a.nim)) || a.nama.localeCompare(b.nama));
  }, [peserta, q, suggestions]);

  if (manual) {
    return (
      <div className="space-y-2">
        <input
          className="input font-mono"
          inputMode="numeric"
          maxLength={12}
          placeholder="Ketik 12 digit NIM"
          value={manualNim}
          onChange={(e) => {
            const v = e.target.value.replace(/\D/g, "").slice(0, 12);
            setManualNim(v);
            if (v.length === 12) onChange(v, "manual");
          }}
        />
        <p className="text-xs text-warn">NIM di luar daftar kelas akan ditandai untuk dicek admin.</p>
        <button type="button" className="text-xs font-semibold text-brand" onClick={() => setManual(false)}>
          ← Kembali pilih dari daftar
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <input className="input" placeholder="Cari nama atau NIM…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="max-h-56 overflow-y-auto rounded-lg border border-line">
        {list.map((p) => {
          const isSug = suggestions.includes(p.nim);
          const done = uploaded?.has(p.nim);
          return (
            <button
              type="button"
              key={p.nim}
              onClick={() => onChange(p.nim, "dropdown")}
              className={`flex w-full items-center gap-2 border-b border-line px-3 py-2 text-left text-sm last:border-0 ${
                value === p.nim ? "bg-brand-soft" : "hover:bg-soft"
              }`}
            >
              <span className="font-mono text-xs text-muted">{p.nim}</span>
              <span className="flex-1 truncate font-medium">{p.nama}</span>
              {isSug && <span className="pill bg-blue-100 text-blue-700">mirip</span>}
              {done && <span className="pill bg-soft text-ink2">sudah</span>}
            </button>
          );
        })}
        {list.length === 0 && <div className="p-3 text-sm text-muted">Tidak ditemukan.</div>}
      </div>
      {allowManual && (
        <button type="button" className="text-xs font-semibold text-brand" onClick={() => setManual(true)}>
          Mahasiswa tidak ada di daftar? Ketik NIM manual
        </button>
      )}
    </div>
  );
}
