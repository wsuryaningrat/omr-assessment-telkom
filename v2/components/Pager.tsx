"use client";

import { useEffect, useMemo, useState } from "react";

export const PAGE_SIZES = [10, 15, 20, 25, 50, 100];
export const DEFAULT_PAGE_SIZE = 15;

/**
 * Paginasi sisi klien. Ukuran halaman disimpan per tabel (`key`) di localStorage.
 * `resetOn` — ubah nilainya (mis. teks pencarian / filter) untuk kembali ke halaman 1.
 * `defaultSize` — ukuran awal bila pengguna belum pernah memilih ukuran untuk tabel ini.
 */
export function usePaged<T>(items: T[], key: string, resetOn: unknown = null, defaultSize = DEFAULT_PAGE_SIZE) {
  const [size, setSizeState] = useState(defaultSize);
  const [page, setPage] = useState(1);

  useEffect(() => {
    try {
      const v = Number(localStorage.getItem(`pager:${key}`));
      if (v > 0) setSizeState(v);
    } catch {}
  }, [key]);

  useEffect(() => setPage(1), [resetOn]);

  const pages = Math.max(1, Math.ceil(items.length / size));
  const cur = Math.min(page, pages);
  const slice = useMemo(() => items.slice((cur - 1) * size, cur * size), [items, cur, size]);

  const setSize = (n: number) => {
    const v = Math.max(1, Math.min(1000, Math.round(n) || defaultSize));
    setSizeState(v);
    setPage(1);
    try {
      localStorage.setItem(`pager:${key}`, String(v));
    } catch {}
  };

  return {
    slice,
    page: cur,
    pages,
    size,
    total: items.length,
    from: items.length ? (cur - 1) * size + 1 : 0,
    to: Math.min(cur * size, items.length),
    setPage,
    setSize,
  };
}

export type Paged = Omit<ReturnType<typeof usePaged>, "slice">;

/** Nomor halaman yang ditampilkan: 1 … 4 5 [6] 7 8 … 20 */
function pageList(page: number, pages: number): (number | "…")[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const out: (number | "…")[] = [1];
  const lo = Math.max(2, page - 1);
  const hi = Math.min(pages - 1, page + 1);
  if (lo > 2) out.push("…");
  for (let i = lo; i <= hi; i++) out.push(i);
  if (hi < pages - 1) out.push("…");
  out.push(pages);
  return out;
}

export default function Pager({ p, className = "" }: { p: Paged; className?: string }) {
  const [custom, setCustom] = useState(false);
  if (p.total === 0) return null;
  const preset = PAGE_SIZES.includes(p.size);
  return (
    <div className={`flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-xs text-ink2 ${className}`}>
      <span className="tabular-nums">
        {p.from}–{p.to} dari <b className="text-ink">{p.total}</b>
      </span>
      {p.pages > 1 && (
        <nav className="flex items-center gap-1" aria-label="Halaman">
          <PageBtn disabled={p.page === 1} onClick={() => p.setPage(p.page - 1)} label="Halaman sebelumnya">
            ‹
          </PageBtn>
          {pageList(p.page, p.pages).map((n, i) =>
            n === "…" ? (
              <span key={`e${i}`} className="px-1 text-ink3">
                …
              </span>
            ) : (
              <PageBtn key={n} active={n === p.page} onClick={() => p.setPage(n)} label={`Halaman ${n}`}>
                {n}
              </PageBtn>
            ),
          )}
          <PageBtn disabled={p.page === p.pages} onClick={() => p.setPage(p.page + 1)} label="Halaman berikutnya">
            ›
          </PageBtn>
        </nav>
      )}
      <label className="flex items-center gap-1.5">
        Per halaman
        {custom || !preset ? (
          <input
            type="number"
            min={1}
            max={1000}
            defaultValue={p.size}
            autoFocus={custom}
            className="w-16 rounded-md border border-line bg-white px-2 py-1 text-xs tabular-nums"
            onBlur={(e) => {
              p.setSize(Number(e.target.value));
              setCustom(false);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
          />
        ) : (
          <select
            value={p.size}
            onChange={(e) => (e.target.value === "custom" ? setCustom(true) : p.setSize(Number(e.target.value)))}
            className="rounded-md border border-line bg-white px-1.5 py-1 text-xs"
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
            <option value="custom">Lainnya…</option>
          </select>
        )}
      </label>
    </div>
  );
}

function PageBtn({
  children,
  onClick,
  active,
  disabled,
  label,
}: {
  children: React.ReactNode;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      className={`min-w-7 rounded-md px-2 py-1 font-semibold tabular-nums transition disabled:opacity-30 ${
        active ? "bg-ink text-white" : "bg-white text-ink2 ring-1 ring-line hover:ring-ink3"
      }`}
    >
      {children}
    </button>
  );
}
