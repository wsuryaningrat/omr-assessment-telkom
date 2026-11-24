"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

export interface SearchOption {
  value: string;
  label?: string;
  /** teks kecil di kanan (mis. jumlah LJK) */
  hint?: string | number;
}

/**
 * Dropdown yang bisa dicari. Pilihan langsung diterapkan lewat `onChange`
 * (tanpa tombol "Terapkan"). Nilai "" = semua.
 */
export default function SearchSelect({
  value,
  onChange,
  options,
  allLabel,
  placeholder = "Cari…",
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  options: SearchOption[];
  allLabel: string;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hi, setHi] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const id = useId();

  const all: SearchOption[] = useMemo(() => [{ value: "", label: allLabel }, ...options], [options, allLabel]);
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return all;
    // pencarian per kata: "if 05" cocok dengan "BS1IF-50-REG05"
    const words = s.split(/\s+/);
    return all.filter((o) => o.value !== "" && words.every((w) => `${o.label ?? o.value} ${o.value}`.toLowerCase().includes(w)));
  }, [all, q]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  useEffect(() => {
    if (open) {
      setQ("");
      setHi(Math.max(0, all.findIndex((o) => o.value === value)));
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-i="${hi}"]`)?.scrollIntoView({ block: "nearest" });
  }, [hi, open]);

  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
  };
  const cur = all.find((o) => o.value === value);

  return (
    <div ref={box} className={`relative ${className}`}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((o) => !o)}
        className={`input flex w-full items-center justify-between gap-2 text-left ${value ? "font-semibold text-ink" : "text-ink2"}`}
      >
        <span className="truncate">{cur?.label ?? cur?.value ?? value}</span>
        <span className="flex items-center gap-1 text-ink3">
          {value && (
            <span
              role="button"
              tabIndex={-1}
              aria-label="Hapus filter"
              onClick={(e) => {
                e.stopPropagation();
                pick("");
              }}
              className="rounded px-1 hover:bg-soft hover:text-ink"
            >
              ✕
            </span>
          )}
          ▾
        </span>
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-full min-w-[16rem] overflow-hidden rounded-lg border border-line bg-white shadow-lg">
          <input
            autoFocus
            value={q}
            placeholder={placeholder}
            onChange={(e) => {
              setQ(e.target.value);
              setHi(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setHi((h) => Math.min(shown.length - 1, h + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setHi((h) => Math.max(0, h - 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                if (shown[hi]) pick(shown[hi].value);
              } else if (e.key === "Escape") {
                setOpen(false);
              }
            }}
            className="w-full border-b border-line px-3 py-2 text-sm outline-none"
          />
          <ul ref={list} id={id} role="listbox" className="max-h-64 overflow-y-auto py-1 text-sm">
            {shown.map((o, i) => (
              <li
                key={o.value || "__all"}
                data-i={i}
                role="option"
                aria-selected={o.value === value}
                onMouseEnter={() => setHi(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(o.value);
                }}
                className={`flex cursor-pointer items-center justify-between gap-3 px-3 py-1.5 ${i === hi ? "bg-soft" : ""} ${
                  o.value === value ? "font-semibold text-brand" : ""
                }`}
              >
                <span className="truncate">{o.label ?? o.value}</span>
                {o.hint !== undefined && <span className="text-xs text-ink3 tabular-nums">{o.hint}</span>}
              </li>
            ))}
            {shown.length === 0 && <li className="px-3 py-2 text-xs text-ink3">Tidak ditemukan.</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
