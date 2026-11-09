import { applyH, localScale } from "./homography";
import type { Homography, ItemStatus, Template } from "./types";

export interface OverlayValues {
  /** Per kolom NIM: digit terpilih atau null. */
  nim: (string | null)[];
  kode: string | null;
  kuisioner: (string | null)[];
  soal: (string | null)[];
}

export interface OverlayStatuses {
  nim?: ItemStatus[];
  kode?: ItemStatus;
  kuisioner?: ItemStatus[];
  soal?: ItemStatus[];
}

export interface OverlayFocus {
  field: "nim" | "kode_soal" | "kuisioner" | "soal";
  index: number;
}

const COLORS = {
  ok: "rgba(22,163,74,0.95)",
  ragu: "rgba(217,119,6,0.95)",
  multi: "rgba(220,38,38,0.95)",
  blank: "rgba(100,116,139,0.55)",
  focus: "rgba(37,99,235,1)",
};

/**
 * Gambar overlay hasil baca di atas foto (canvas 2D).
 * `scale` = ukuran canvas / ukuran gambar asli (H dalam koordinat gambar asli).
 */
export function drawOmrOverlay(
  ctx: CanvasRenderingContext2D,
  H: Homography,
  tpl: Template,
  values: OverlayValues,
  statuses: OverlayStatuses,
  scale: number,
  focus?: OverlayFocus | null,
) {
  const f = tpl.fields;
  const lw = Math.max(1.5, 2 * scale * (localScale(H, 105, 148) / 6));

  const drawItem = (
    field: OverlayFocus["field"],
    index: number,
    cells: [number, number][],
    options: string[],
    r: number,
    value: string | null,
    status: ItemStatus | undefined,
  ) => {
    const isFocus = focus && focus.field === field && focus.index === index;
    const st = status ?? (value ? "ok" : "blank");
    cells.forEach(([x, y], o) => {
      const p = applyH(H, x, y);
      const rad = r * localScale(H, x, y) * scale;
      const chosen = value !== null && options[o] === value;
      if (!chosen && !isFocus && st !== "multi" && st !== "ragu") return;
      ctx.beginPath();
      ctx.arc(p.x * scale, p.y * scale, rad * 1.15, 0, Math.PI * 2);
      if (chosen) {
        ctx.strokeStyle = st === "ragu" ? COLORS.ragu : COLORS.ok;
        ctx.lineWidth = lw * 1.4;
      } else if (st === "multi") {
        ctx.strokeStyle = COLORS.multi;
        ctx.lineWidth = lw;
      } else if (st === "ragu") {
        ctx.strokeStyle = "rgba(217,119,6,0.45)";
        ctx.lineWidth = lw * 0.8;
      } else {
        ctx.strokeStyle = COLORS.blank;
        ctx.lineWidth = lw * 0.7;
      }
      ctx.stroke();
    });
    if (isFocus) {
      const a = applyH(H, cells[0][0] - r * 2.2, cells[0][1] - r * 1.6);
      const b = applyH(H, cells[cells.length - 1][0] + r * 1.6, cells[cells.length - 1][1] + r * 1.6);
      ctx.strokeStyle = COLORS.focus;
      ctx.lineWidth = lw * 1.2;
      ctx.strokeRect(a.x * scale, a.y * scale, (b.x - a.x) * scale, (b.y - a.y) * scale);
    }
  };

  f.nim.items.forEach((it, i) => drawItem("nim", i, it.cells, f.nim.options, f.nim.r, values.nim[i] ?? null, statuses.nim?.[i]));
  drawItem("kode_soal", 0, f.kode_soal.items[0].cells, f.kode_soal.options, f.kode_soal.r, values.kode, statuses.kode);
  f.kuisioner.items.forEach((it, i) =>
    drawItem("kuisioner", i, it.cells, f.kuisioner.options, f.kuisioner.r, values.kuisioner[i] ?? null, statuses.kuisioner?.[i]),
  );
  f.soal.items.forEach((it, i) => drawItem("soal", i, it.cells, f.soal.options, f.soal.r, values.soal[i] ?? null, statuses.soal?.[i]));

  // Titik pojok
  ctx.fillStyle = "rgba(37,99,235,0.9)";
  for (const k of ["tl", "tr", "br", "bl"] as const) {
    const [x, y] = tpl.marker.centers[k];
    const p = applyH(H, x, y);
    ctx.beginPath();
    ctx.arc(p.x * scale, p.y * scale, lw * 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
}
