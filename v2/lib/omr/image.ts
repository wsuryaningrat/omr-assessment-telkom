import type { Gray } from "./types";

/**
 * RGBA -> grayscale, dengan downscale box-average opsional (faktor bulat) agar
 * cepat untuk deteksi live. Mengembalikan gambar dan faktor skala (out = in * scale).
 */
export function rgbaToGray(
  rgba: Uint8ClampedArray | Uint8Array,
  w: number,
  h: number,
  maxSide?: number,
): { gray: Gray; scale: number } {
  const f = maxSide && Math.max(w, h) > maxSide ? Math.ceil(Math.max(w, h) / maxSide) : 1;
  const ow = Math.floor(w / f);
  const oh = Math.floor(h / f);
  const out = new Uint8Array(ow * oh);
  if (f === 1) {
    for (let i = 0, j = 0; i < ow * oh; i++, j += 4) {
      // luma BT.601 (integer)
      out[i] = (rgba[j] * 77 + rgba[j + 1] * 150 + rgba[j + 2] * 29) >> 8;
    }
    return { gray: { w: ow, h: oh, data: out }, scale: 1 };
  }
  const n = f * f;
  for (let y = 0; y < oh; y++) {
    for (let x = 0; x < ow; x++) {
      let s = 0;
      for (let dy = 0; dy < f; dy++) {
        let j = ((y * f + dy) * w + x * f) * 4;
        for (let dx = 0; dx < f; dx++, j += 4) {
          s += rgba[j] * 77 + rgba[j + 1] * 150 + rgba[j + 2] * 29;
        }
      }
      out[y * ow + x] = (s / n) >> 8;
    }
  }
  return { gray: { w: ow, h: oh, data: out }, scale: 1 / f };
}

/** Downscale gambar gray dengan faktor bulat (box average). */
export function downscaleGray(g: Gray, maxSide: number): { gray: Gray; scale: number } {
  const f = Math.max(g.w, g.h) > maxSide ? Math.ceil(Math.max(g.w, g.h) / maxSide) : 1;
  if (f === 1) return { gray: g, scale: 1 };
  const ow = Math.floor(g.w / f);
  const oh = Math.floor(g.h / f);
  const out = new Uint8Array(ow * oh);
  const n = f * f;
  for (let y = 0; y < oh; y++) {
    for (let x = 0; x < ow; x++) {
      let s = 0;
      for (let dy = 0; dy < f; dy++) {
        const row = (y * f + dy) * g.w + x * f;
        for (let dx = 0; dx < f; dx++) s += g.data[row + dx];
      }
      out[y * ow + x] = Math.round(s / n);
    }
  }
  return { gray: { w: ow, h: oh, data: out }, scale: 1 / f };
}

/** Interpolasi bilinear; di luar gambar mengembalikan NaN. */
export function bilinear(g: Gray, x: number, y: number): number {
  if (x < 0 || y < 0 || x > g.w - 1 || y > g.h - 1) return NaN;
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const x1 = Math.min(x0 + 1, g.w - 1);
  const y1 = Math.min(y0 + 1, g.h - 1);
  const fx = x - x0;
  const fy = y - y0;
  const d = g.data;
  const a = d[y0 * g.w + x0];
  const b = d[y0 * g.w + x1];
  const c = d[y1 * g.w + x0];
  const e = d[y1 * g.w + x1];
  return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + e * fx) * fy;
}

/**
 * Threshold adaptif (Bradley): piksel dianggap gelap jika < mean_lokal * (1 - t).
 * Window besar (~1/7 sisi pendek) supaya kotak hitam 10 mm tetap pejal.
 */
export function adaptiveThreshold(g: Gray, win: number, t: number): Uint8Array {
  const { w, h, data } = g;
  const W = w + 1;
  const integ = new Float64Array(W * (h + 1));
  for (let y = 0; y < h; y++) {
    let rowSum = 0;
    for (let x = 0; x < w; x++) {
      rowSum += data[y * w + x];
      integ[(y + 1) * W + x + 1] = integ[y * W + x + 1] + rowSum;
    }
  }
  const half = Math.max(1, Math.floor(win / 2));
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y - half);
    const y1 = Math.min(h - 1, y + half);
    for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - half);
      const x1 = Math.min(w - 1, x + half);
      const count = (x1 - x0 + 1) * (y1 - y0 + 1);
      const sum =
        integ[(y1 + 1) * W + x1 + 1] - integ[y0 * W + x1 + 1] - integ[(y1 + 1) * W + x0] + integ[y0 * W + x0];
      if (data[y * w + x] * count < sum * (1 - t)) out[y * w + x] = 1;
    }
  }
  return out;
}

/** Ambang Otsu untuk sekumpulan nilai 0..255. */
export function otsu(values: ArrayLike<number>): number {
  const hist = new Float64Array(256);
  for (let i = 0; i < values.length; i++) hist[Math.max(0, Math.min(255, values[i] | 0))]++;
  const total = values.length;
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * hist[i];
  let sumB = 0;
  let wB = 0;
  let best = 0;
  let thr = 127;
  for (let i = 0; i < 256; i++) {
    wB += hist[i];
    if (wB === 0) continue;
    const wF = total - wB;
    if (wF === 0) break;
    sumB += i * hist[i];
    const mB = sumB / wB;
    const mF = (sum - sumB) / wF;
    const between = wB * wF * (mB - mF) * (mB - mF);
    if (between > best) {
      best = between;
      thr = i;
    }
  }
  return thr;
}

export function percentile(arr: number[], p: number): number {
  if (arr.length === 0) return NaN;
  const s = [...arr].sort((a, b) => a - b);
  const idx = Math.min(s.length - 1, Math.max(0, Math.round((p / 100) * (s.length - 1))));
  return s[idx];
}
