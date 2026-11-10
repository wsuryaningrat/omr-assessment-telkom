import { applyH, localScale } from "./homography";
import { bilinear, percentile } from "./image";
import type { Gray, Homography, Template } from "./types";

export type FieldName = keyof Template["fields"];
export const FIELD_NAMES: FieldName[] = ["nim", "kode_soal", "kuisioner", "soal"];

/** Kegelapan mentah tiap bubble: 1 - mean(dalam) / putih_lokal, 0..1. */
export type RawRead = Record<FieldName, number[][]>;

// Pola titik sampel dalam satuan jari-jari (dibuat sekali).
const INNER: [number, number][] = [];
for (let i = -6; i <= 6; i++)
  for (let j = -6; j <= 6; j++) {
    const x = i / 6;
    const y = j / 6;
    if (x * x + y * y <= 1) INNER.push([x * 0.7, y * 0.7]);
  }
const RING: [number, number][] = [];
for (const rr of [1.32, 1.5]) for (let i = 0; i < 24; i++) {
  const a = (i / 24) * Math.PI * 2;
  RING.push([Math.cos(a) * rr, Math.sin(a) * rr]);
}

// Grid cakupan: hampir seluruh isi bulatan (85% jari-jari, di dalam garis cetak).
const COVER: [number, number][] = [];
for (let i = -8; i <= 8; i++)
  for (let j = -8; j <= 8; j++) {
    const x = i / 8;
    const y = j / 8;
    if (x * x + y * y <= 1) COVER.push([x * 0.85, y * 0.85]);
  }
/** Tingkat kegelapan piksel (1 - v/putih) untuk menghitung cakupan; dipilih per lembar di decode. */
export const PIX_LEVELS = [0.12, 0.16, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5];
/** Cakupan mentah tiap bubble: cover[field][item][opsi][k] = proporsi area dgn kegelapan >= PIX_LEVELS[k]. */
export type RawCover = Record<FieldName, number[][][]>;

/** Ambang "tinta pekat" relatif terhadap putih lokal. */
const INK_LEVEL = 0.68;
/** Di bawah proporsi ini, kegelapan dianggap abu-abu rata (bekas hapusan / bayangan). */
const MIN_INK_FRAC = 0.08;
const SMUDGE_WEIGHT = 0.4;

/**
 * Kegelapan efektif bubble (0..1). Bubble yang gelapnya rata tanpa piksel tinta pekat
 * (bekas hapusan, bayangan) dibobot turun agar tidak terbaca sebagai jawaban.
 */
export function bubbleDarkness(g: Gray, H: Homography, cx: number, cy: number, r: number): number {
  const inner: number[] = [];
  for (const [dx, dy] of INNER) {
    const p = applyH(H, cx + dx * r, cy + dy * r);
    const v = bilinear(g, p.x, p.y);
    if (!Number.isNaN(v)) inner.push(v);
  }
  const n = inner.length;
  const s = inner.reduce((a, b) => a + b, 0);
  const ring: number[] = [];
  for (const [dx, dy] of RING) {
    const p = applyH(H, cx + dx * r, cy + dy * r);
    const v = bilinear(g, p.x, p.y);
    if (!Number.isNaN(v)) ring.push(v);
  }
  if (n === 0 || ring.length < 8) return NaN;
  const white = percentile(ring, 75);
  if (!(white > 0)) return NaN;
  const d = Math.max(0, Math.min(1, 1 - s / n / white));
  const ink = inner.filter((v) => v < white * INK_LEVEL).length / n;
  return ink < MIN_INK_FRAC ? d * SMUDGE_WEIGHT : d;
}

/** Proporsi area bulatan yang tertutup tinta, untuk tiap tingkat di PIX_LEVELS. */
export function bubbleCoverage(g: Gray, H: Homography, cx: number, cy: number, r: number): number[] {
  const ring: number[] = [];
  for (const [dx, dy] of RING) {
    const p = applyH(H, cx + dx * r, cy + dy * r);
    const v = bilinear(g, p.x, p.y);
    if (!Number.isNaN(v)) ring.push(v);
  }
  const white = ring.length >= 8 ? percentile(ring, 75) : NaN;
  if (!(white > 0)) return PIX_LEVELS.map(() => 0);
  const cnt = PIX_LEVELS.map(() => 0);
  let n = 0;
  for (const [dx, dy] of COVER) {
    const p = applyH(H, cx + dx * r, cy + dy * r);
    const v = bilinear(g, p.x, p.y);
    if (Number.isNaN(v)) continue;
    n++;
    const dark = 1 - v / white;
    for (let k = 0; k < PIX_LEVELS.length; k++) if (dark >= PIX_LEVELS[k]) cnt[k]++;
  }
  return cnt.map((c) => (n ? c / n : 0));
}

/** Baca kegelapan + cakupan semua bubble. */
export function readSheetFull(g: Gray, H: Homography, tpl: Template): { raw: RawRead; cover: RawCover } {
  const raw = {} as RawRead;
  const cover = {} as RawCover;
  for (const name of FIELD_NAMES) {
    const f = tpl.fields[name];
    raw[name] = f.items.map((item) => item.cells.map(([x, y]) => bubbleDarkness(g, H, x, y, f.r)));
    cover[name] = f.items.map((item) => item.cells.map(([x, y]) => bubbleCoverage(g, H, x, y, f.r)));
  }
  return { raw, cover };
}

/** Baca kegelapan semua bubble pada semua field. */
export function readSheet(g: Gray, H: Homography, tpl: Template): RawRead {
  const out = {} as RawRead;
  for (const name of FIELD_NAMES) {
    const f = tpl.fields[name];
    out[name] = f.items.map((item) => item.cells.map(([x, y]) => bubbleDarkness(g, H, x, y, f.r)));
  }
  return out;
}

/** Posisi piksel pusat bubble (untuk overlay). */
export function bubbleCenters(H: Homography, tpl: Template) {
  const out: { field: FieldName; item: number; opt: number; x: number; y: number; r: number }[] = [];
  for (const name of FIELD_NAMES) {
    const f = tpl.fields[name];
    f.items.forEach((item, i) =>
      item.cells.forEach(([x, y], o) => {
        const p = applyH(H, x, y);
        out.push({ field: name, item: i, opt: o, x: p.x, y: p.y, r: f.r * localScale(H, x, y) });
      }),
    );
  }
  return out;
}
