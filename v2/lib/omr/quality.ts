import { applyH, localScale } from "./homography";
import { bilinear } from "./image";
import { quadAngles } from "./markers";
import type { Corners, Gray, Homography, QualityReport, Template } from "./types";

export const QUALITY_LIMITS = {
  /** Minimal piksel per mm pada kertas (≈ lebar kertas 800 px). */
  minPxPerMm: 3.8,
  /** Ketajaman minimal (Laplacian ternormalisasi pada warp 4 px/mm). */
  minSharpness: 0.035,
  minAngle: 62,
  maxAngle: 118,
};

/**
 * Ketajaman: warp area jawaban soal ke grid 4 px/mm, hitung rata-rata |Laplacian|
 * dibagi kontras. Tidak bergantung resolusi kamera.
 */
export function sharpness(g: Gray, H: Homography, tpl: Template): number {
  const items = tpl.fields.soal.items;
  const xs = items.flatMap((i) => i.cells.map((c) => c[0]));
  const ys = items.flatMap((i) => i.cells.map((c) => c[1]));
  const x0 = Math.min(...xs) - 4;
  const y0 = Math.min(...ys) - 4;
  const x1 = Math.max(...xs) + 4;
  const y1 = Math.min(Math.max(...ys), y0 + 60) + 4;
  const step = 0.25;
  const W = Math.floor((x1 - x0) / step);
  const Hh = Math.floor((y1 - y0) / step);
  const buf = new Float32Array(W * Hh);
  let lo = 255;
  let hi = 0;
  for (let j = 0; j < Hh; j++)
    for (let i = 0; i < W; i++) {
      const p = applyH(H, x0 + i * step, y0 + j * step);
      const v = bilinear(g, p.x, p.y);
      const vv = Number.isNaN(v) ? 255 : v;
      buf[j * W + i] = vv;
      if (vv < lo) lo = vv;
      if (vv > hi) hi = vv;
    }
  const contrast = Math.max(20, hi - lo);
  let s = 0;
  let n = 0;
  for (let j = 1; j < Hh - 1; j++)
    for (let i = 1; i < W - 1; i++) {
      const c = buf[j * W + i];
      const lap = 4 * c - buf[j * W + i - 1] - buf[j * W + i + 1] - buf[(j - 1) * W + i] - buf[(j + 1) * W + i];
      s += Math.abs(lap);
      n++;
    }
  return n ? s / n / contrast : 0;
}

export function assessQuality(g: Gray, H: Homography, corners: Corners, tpl: Template): QualityReport {
  const { w, h } = tpl.page;
  const pxPerMm =
    (localScale(H, w * 0.25, h * 0.25) +
      localScale(H, w * 0.75, h * 0.25) +
      localScale(H, w * 0.25, h * 0.75) +
      localScale(H, w * 0.75, h * 0.75)) /
    4;
  const angles = quadAngles(corners);
  const minAngle = Math.min(...angles);
  const maxAngle = Math.max(...angles);
  const sharp = sharpness(g, H, tpl);
  const L = QUALITY_LIMITS;
  const issues: string[] = [];
  if (pxPerMm < L.minPxPerMm) issues.push("Terlalu jauh — dekatkan kamera ke lembar.");
  if (minAngle < L.minAngle || maxAngle > L.maxAngle) issues.push("Terlalu miring — posisikan HP sejajar di atas lembar.");
  if (sharp < L.minSharpness) issues.push("Foto buram — tahan HP lebih stabil / tunggu fokus.");
  return {
    pxPerMm: Math.round(pxPerMm * 100) / 100,
    sharpness: Math.round(sharp * 1000) / 1000,
    minAngle: Math.round(minAngle),
    maxAngle: Math.round(maxAngle),
    issues,
    ok: issues.length === 0,
  };
}

/** Titik pojok kertas (bukan marker) dalam piksel — untuk menggambar bingkai di kamera. */
export function pageOutline(H: Homography, tpl: Template) {
  const { w, h } = tpl.page;
  return [applyH(H, 0, 0), applyH(H, w, 0), applyH(H, w, h), applyH(H, 0, h)];
}
