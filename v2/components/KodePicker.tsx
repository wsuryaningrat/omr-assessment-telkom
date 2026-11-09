"use client";

import { useState } from "react";

/**
 * Pilih kode soal: tombol untuk kode yang berlaku di ujian ini + input manual 3 digit.
 * `read` = hasil OMR (boleh null / tidak ada di daftar).
 */
export default function KodePicker({
  kodeList,
  value,
  read,
  onChange,
  disabled = false,
}: {
  kodeList: string[];
  value: string | null;
  read: string | null;
  onChange: (kode: string) => void;
  disabled?: boolean;
}) {
  const [manual, setManual] = useState(!!value && !kodeList.includes(value));
  const readUnknown = read && !kodeList.includes(read);
  return (
    <div className="space-y-2">
      {kodeList.length > 0 && (
        <div className="grid grid-cols-4 gap-2">
          {kodeList.map((k) => (
            <button
              key={k}
              type="button"
              disabled={disabled}
              onClick={() => {
                setManual(false);
                onChange(k);
              }}
              className={`rounded-lg border py-2.5 font-mono text-sm font-bold ${value === k ? "border-brand bg-brand text-white" : "border-line bg-white"}`}
            >
              {k}
            </button>
          ))}
        </div>
      )}
      {readUnknown && (
        <p className="text-xs text-warn">
          Terbaca <b className="font-mono">{read}</b>, tidak ada di daftar kode ujian ini. Cocokkan dengan tulisan di LJK.
        </p>
      )}
      {manual || kodeList.length === 0 ? (
        <input
          className="input max-w-40 font-mono"
          inputMode="numeric"
          maxLength={3}
          placeholder="3 digit"
          disabled={disabled}
          defaultValue={value && !kodeList.includes(value) ? value : ""}
          onChange={(e) => {
            const v = e.target.value.replace(/\D/g, "").slice(0, 3);
            e.target.value = v;
            if (v.length === 3) onChange(v);
          }}
        />
      ) : (
        !disabled && (
          <button type="button" className="text-xs font-semibold text-brand" onClick={() => setManual(true)}>
            Kode lain? Ketik manual
          </button>
        )
      )}
    </div>
  );
}
