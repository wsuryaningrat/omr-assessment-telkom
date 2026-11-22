import { applyH, localScale } from "./homography";
import { bilinear, percentile } from "./image";
import type { ItemOffsets } from "./align";
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

/** Ciri satu bubble: gelap rata-rata (tanpa pembobotan), proporsi piksel tinta pekat, tekstur (sebaran). */
export interface BubbleFeat {
  dark: number;
  ink: number;
  tex: number;
}

export function bubbleFeatures(g: Gray, H: Homography, cx: number, cy: number, r: number): BubbleFeat {
  const inner: number[] = [];
  for (const [dx, dy] of INNER) {
    const p = applyH(H, cx + dx * r, cy + dy * r);
    const v = bilinear(g, p.x, p.y);
    if (!Number.isNaN(v)) inner.push(v);
  }
  const ring: number[] = [];
  for (const [dx, dy] of RING) {
    const p = applyH(H, cx + dx * r, cy + dy * r);
    const v = bilinear(g, p.x, p.y);
    if (!Number.isNaN(v)) ring.push(v);
  }
  const n = inner.length;
  if (n === 0 || ring.length < 8) return { dark: NaN, ink: NaN, tex: NaN };
  const white = percentile(ring, 75);
  if (!(white > 0)) return { dark: NaN, ink: NaN, tex: NaN };
  const mean = inner.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(inner.reduce((a, b) => a + (b - mean) ** 2, 0) / n);
  return {
    dark: Math.max(0, Math.min(1, 1 - mean / white)),
    ink: inner.filter((v) => v < white * INK_LEVEL).length / n,
    tex: sd / white,
  };
}

export function readFeatures(g: Gray, H: Homography, tpl: Template, off?: ItemOffsets): Record<FieldName, BubbleFeat[][]> {
  const out = {} as Record<FieldName, BubbleFeat[][]>;
  for (const name of FIELD_NAMES) {
    const f = tpl.fields[name];
    out[name] = f.items.map((item, i) => {
      const [dx, dy] = off?.[name][i] ?? [0, 0];
      return item.cells.map(([x, y]) => bubbleFeatures(g, H, x + dx, y + dy, f.r));
    });
  }
  return out;
}

/** Kegelapan efektif dari ciri bubble (sama dengan bubbleDarkness). */
export const effectiveDarkness = (f: BubbleFeat) => (Number.isNaN(f.dark) ? NaN : f.ink < MIN_INK_FRAC ? f.dark * SMUDGE_WEIGHT : f.dark);

/**
 * Baca semua bubble pada semua field (dengan geseran lokal per nomor bila ada).
 * `raw` = kegelapan efektif (bekas hapusan/bayangan dibobot turun), `dark` = kegelapan apa adanya dan
 * `tex` = tekstur (sebaran) — keduanya dipakai decode untuk menerima arsiran pensil tipis yang berdiri sendiri.
 */
export function readSheetFull(g: Gray, H: Homography, tpl: Template, off?: ItemOffsets): { raw: RawRead; dark: RawRead; tex: RawRead } {
  const feat = readFeatures(g, H, tpl, off);
  const raw = {} as RawRead;
  const dark = {} as RawRead;
  const tex = {} as RawRead;
  for (const name of FIELD_NAMES) {
    raw[name] = feat[name].map((row) => row.map(effectiveDarkness));
    dark[name] = feat[name].map((row) => row.map((f) => f.dark));
    tex[name] = feat[name].map((row) => row.map((f) => f.tex));
  }
  return { raw, dark, tex };
}

/** Kegelapan efektif semua bubble. */
export function readSheet(g: Gray, H: Homography, tpl: Template, off?: ItemOffsets): RawRead {
  return readSheetFull(g, H, tpl, off).raw;
}

/** Posisi piksel pusat bubble (untuk overlay). */
export function bubbleCenters(H: Homography, tpl: Template, off?: ItemOffsets) {
  const out: { field: FieldName; item: number; opt: number; x: number; y: number; r: number }[] = [];
  for (const name of FIELD_NAMES) {
    const f = tpl.fields[name];
    f.items.forEach((item, i) =>
      item.cells.forEach(([x0, y0], o) => {
        const [dx, dy] = off?.[name][i] ?? [0, 0];
        const x = x0 + dx;
        const y = y0 + dy;
        const p = applyH(H, x, y);
        out.push({ field: name, item: i, opt: o, x: p.x, y: p.y, r: f.r * localScale(H, x, y) });
      }),
    );
  }
  return out;
}
