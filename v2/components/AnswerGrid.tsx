"use client";

import type { ItemStatus } from "@/lib/omr/types";

const STATUS_STYLE: Record<ItemStatus, string> = {
  ok: "border-l-transparent",
  blank: "border-l-slate-300",
  ragu: "border-l-amber-500 bg-warn-bg",
  multi: "border-l-red-500 bg-bad-bg",
};

export default function AnswerGrid({
  title,
  options,
  values,
  statuses,
  original,
  onChange,
  onFocus,
  onlyFlagged = false,
  readOnly = false,
}: {
  title: string;
  options: string[];
  values: (string | null)[];
  statuses?: ItemStatus[];
  /** Nilai hasil OMR awal, untuk menandai yang sudah dikoreksi. */
  original?: (string | null)[];
  onChange?: (index: number, value: string | null) => void;
  onFocus?: (index: number) => void;
  onlyFlagged?: boolean;
  readOnly?: boolean;
}) {
  const idx = values
    .map((_, i) => i)
    .filter((i) => !onlyFlagged || (statuses && (statuses[i] === "ragu" || statuses[i] === "multi")));
  const filled = values.filter(Boolean).length;
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-bold">{title}</h3>
        <span className="text-xs text-muted">
          {filled}/{values.length} terisi
          {statuses && (
            <>
              {" · "}
              <span className="text-warn">{statuses.filter((s) => s === "ragu").length} ragu</span>
              {" · "}
              <span className="text-bad">{statuses.filter((s) => s === "multi").length} ganda</span>
            </>
          )}
        </span>
      </div>
      {idx.length === 0 ? (
        <p className="rounded-lg bg-soft p-3 text-xs text-muted">Tidak ada yang ditandai.</p>
      ) : (
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-3 lg:grid-cols-3">
          {idx.map((i) => {
            const st = statuses?.[i] ?? "ok";
            const edited = original && original[i] !== values[i];
            return (
              <div
                key={i}
                title={st === "multi" ? "OMR: jawaban ganda" : st === "ragu" ? "OMR: ragu (arsiran tipis / mirip)" : undefined}
                className={`flex items-center gap-1.5 rounded-md border-l-4 py-0.5 pr-1 pl-1.5 ${STATUS_STYLE[st]}`}
                onMouseEnter={() => onFocus?.(i)}
                onFocus={() => onFocus?.(i)}
              >
                <span className="w-6 shrink-0 text-right text-xs font-semibold tabular-nums text-ink2">{i + 1}</span>
                {options.map((o) => {
                  const on = values[i] === o;
                  return (
                    <button
                      key={o}
                      type="button"
                      disabled={readOnly}
                      onClick={() => onChange?.(i, on ? null : o)}
                      className={`h-7 w-7 shrink-0 rounded-full border text-[11px] font-bold transition ${
                        on ? (edited ? "border-blue-600 bg-blue-600 text-white" : "border-ink bg-ink text-white") : "border-slate-300 bg-white text-slate-500 hover:border-slate-500"
                      }`}
                    >
                      {o}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
