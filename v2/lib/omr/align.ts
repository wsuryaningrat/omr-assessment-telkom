import { applyH } from "./homography";
import { bilinear, percentile } from "./image";
import type { Gray, Homography, Template } from "./types";

/**
 * Penyelarasan lokal posisi bubble.
 *
 * Homography dari 4 kotak hitam hanya benar bila kertas datar. Di lapangan kertas sering melengkung /
 * terlipat saat dipegang dan lensa HP sedikit cembung, sehingga bubble di tengah lembar bisa meleset
 * 1–3 mm dari posisi hitungan (lingkaran overlay "tidak pas", arsiran pekat terbaca setengah → ragu).
 *
 * Langkah:
 *  1. Untuk tiap nomor, cari geseran (dx, dy) ≤ SEARCH mm yang membuat salah satu opsinya paling gelap
 *     (pusat tinta arsiran). Nomor dengan arsiran jelas menjadi titik acuan.
 *  2. Dari semua titik acuan, cocokkan medan geseran halus (polinomial kubik pada posisi lembar, dengan
 *     penolakan pencilan) — kertas melengkung itu mulus, jadi nomor kosong ikut terkoreksi.
 *  3. Tiap nomor diberi geseran medan + koreksi kecil lokal (≤ REFINE mm) ke arah pusat tinta.
 * Semua geseran dalam mm koordinat template; dipakai saat membaca dan saat menggambar overlay.
 */

export type ItemOffsets = Record<"nim" | "kode_soal" | "kuisioner" | "soal", [number, number][]>;
const FIELDS = ["nim", "kode_soal", "kuisioner", "soal"] as const;

/** Batas pencarian geseran per nomor (mm). Harus < setengah jarak antar-bubble (5.4 mm). */
const SEARCH = 2.4;
/** Koreksi lokal maksimum di atas medan halus (mm). */
const REFINE = 0.6;
const MAX_SHIFT = 6.0;

const RING: [number, number][] = [];
for (let i = 0; i < 16; i++) {
  const a = (i / 16) * Math.PI * 2;
  RING.push([Math.cos(a) * 1.45, Math.sin(a) * 1.45]);
}

/** Putih kertas di sekitar satu nomor (persentil atas cincin luar semua opsinya). */
function itemWhite(g: Gray, H: Homography, cells: [number, number][], r: number): number {
  const vals: number[] = [];
  for (const [cx, cy] of cells)
    for (const [dx, dy] of RING) {
      const p = applyH(H, cx + dx * r, cy + dy * r);
      const v = bilinear(g, p.x, p.y);
      if (!Number.isNaN(v)) vals.push(v);
    }
  return vals.length ? percentile(vals, 80) : NaN;
}

/**
 * Potongan gambar yang sudah "diluruskan" ke koordinat template (resolusi PX mm) + integral image,
 * supaya rata-rata kegelapan di sembarang posisi dihitung O(1) — pencarian geseran jadi sangat murah.
 */
const PX = 0.2;
interface Patch {
  x0: number;
  y0: number;
  w: number;
  h: number;
  ii: Float64Array;
  cnt: Float64Array;
}

function makePatch(g: Gray, H: Homography, cells: [number, number][], r: number, ox: number, oy: number, lim: number): Patch {
  const pad = r + lim + 2 * PX;
  const xs = cells.map((c) => c[0] + ox);
  const ys = cells.map((c) => c[1] + oy);
  const x0 = Math.min(...xs) - pad;
  const y0 = Math.min(...ys) - pad;
  const w = Math.ceil((Math.max(...xs) + pad - x0) / PX) + 1;
  const h = Math.ceil((Math.max(...ys) + pad - y0) / PX) + 1;
  const W = w + 1;
  const ii = new Float64Array(W * (h + 1));
  const cnt = new Float64Array(W * (h + 1));
  // H terproyeksi: hitung baris demi baris (pembagian perspektif per titik tetap perlu)
  for (let j = 0; j < h; j++) {
    let rs = 0;
    let rc = 0;
    const ty = y0 + j * PX;
    for (let i = 0; i < w; i++) {
      const p = applyH(H, x0 + i * PX, ty);
      const v = bilinear(g, p.x, p.y);
      if (!Number.isNaN(v)) {
        rs += v;
        rc++;
      }
      const k = (j + 1) * W + (i + 1);
      ii[k] = ii[k - W] + rs;
      cnt[k] = cnt[k - W] + rc;
    }
  }
  return { x0, y0, w, h, ii, cnt };
}

/** Rata-rata intensitas di persegi luas-setara disk (jari-jari 0.8 r) berpusat di (x, y) mm. */
function patchMean(P: Patch, x: number, y: number, r: number): number {
  const half = (0.8 * r * 0.886) / PX;
  const cx = (x - P.x0) / PX;
  const cy = (y - P.y0) / PX;
  const i0 = Math.max(0, Math.round(cx - half));
  const i1 = Math.min(P.w, Math.round(cx + half) + 1);
  const j0 = Math.max(0, Math.round(cy - half));
  const j1 = Math.min(P.h, Math.round(cy + half) + 1);
  if (i1 <= i0 || j1 <= j0) return NaN;
  const W = P.w + 1;
  const s = P.ii[j1 * W + i1] - P.ii[j0 * W + i1] - P.ii[j1 * W + i0] + P.ii[j0 * W + i0];
  const n = P.cnt[j1 * W + i1] - P.cnt[j0 * W + i1] - P.cnt[j1 * W + i0] + P.cnt[j0 * W + i0];
  return n ? s / n : NaN;
}

/** Kegelapan opsi tergelap & kedua pada geseran (dx,dy), relatif terhadap putih. */
function bestDark(P: Patch, cells: [number, number][], r: number, white: number, dx: number, dy: number) {
  let best = 0;
  let second = 0;
  for (const [cx, cy] of cells) {
    const m = patchMean(P, cx + dx, cy + dy, r);
    if (Number.isNaN(m)) continue;
    const d = 1 - m / white;
    if (d > best) {
      second = best;
      best = d;
    } else if (d > second) second = d;
  }
  return { best, second };
}

/** Cari geseran yang memaksimalkan kegelapan opsi tergelap, dalam kotak ±lim di sekitar (ox, oy). */
function searchOffset(
  g: Gray,
  H: Homography,
  cells: [number, number][],
  r: number,
  white: number,
  ox: number,
  oy: number,
  lim: number,
) {
  const P = makePatch(g, H, cells, r, ox, oy, lim);
  let bx = ox;
  let by = oy;
  let bv = -1;
  let b2 = 0;
  const n = Math.round(lim / PX);
  for (let i = -n; i <= n; i++)
    for (let j = -n; j <= n; j++) {
      const dx = ox + i * PX;
      const dy = oy + j * PX;
      const { best, second } = bestDark(P, cells, r, white, dx, dy);
      // sedikit hukuman jarak agar tidak lari tanpa alasan pada area rata
      const v = best - 0.01 * Math.hypot(i * PX, j * PX);
      if (v > bv) {
        bv = v;
        bx = dx;
        by = dy;
        b2 = second;
      }
    }
  const base = bestDark(P, cells, r, white, ox, oy).best;
  return { dx: bx, dy: by, dark: bv, second: b2, base };
}

// --------------------------------------------------------------- medan geseran halus (polinomial)
function basis(x: number, y: number, tpl: Template): number[] {
  const u = (x - tpl.page.w / 2) / (tpl.page.w / 2);
  const v = (y - tpl.page.h / 2) / (tpl.page.h / 2);
  return [1, u, v, u * u, u * v, v * v, u * u * u, u * u * v, u * v * v, v * v * v];
}

/** Kuadrat terkecil ber-ridge (dengan bobot) — kecil & stabil, tanpa pustaka. */
function fitRidge(rows: number[][], ys: number[], ws: number[], lambda: number): number[] {
  const k = rows[0].length;
  const A = Array.from({ length: k }, () => new Array(k + 1).fill(0));
  rows.forEach((b, n) => {
    for (let i = 0; i < k; i++) {
      for (let j = 0; j < k; j++) A[i][j] += ws[n] * b[i] * b[j];
      A[i][k] += ws[n] * b[i] * ys[n];
    }
  });
  for (let i = 0; i < k; i++) A[i][i] += lambda * (i === 0 ? 0.01 : 1);
  for (let c = 0; c < k; c++) {
    let p = c;
    for (let r = c + 1; r < k; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]];
    if (Math.abs(A[c][c]) < 1e-12) continue;
    for (let r = 0; r < k; r++) {
      if (r === c) continue;
      const f = A[r][c] / A[c][c];
      for (let j = c; j <= k; j++) A[r][j] -= f * A[c][j];
    }
  }
  return A.map((row, i) => (Math.abs(row[i]) < 1e-12 ? 0 : row[k] / row[i]));
}

const dot = (a: number[], b: number[]) => a.reduce((s, v, i) => s + v * b[i], 0);

export interface AlignResult {
  offsets: ItemOffsets;
  /** Banyak nomor yang dipakai sebagai acuan & sisa galat medan (mm), untuk diagnosis. */
  anchors: number;
  rms: number;
}

type Field = (typeof FIELDS)[number];
type Anchor = { x: number; y: number; dx: number; dy: number; w: number };

function itemCenter(cells: [number, number][]): [number, number] {
  return [cells.reduce((s, c) => s + c[0], 0) / cells.length, cells.reduce((s, c) => s + c[1], 0) / cells.length];
}

/** Cocokkan medan halus dari titik acuan, dengan penolakan pencilan. */
function fitField(pts: Anchor[], tpl: Template) {
  let cx = new Array(10).fill(0);
  let cy = new Array(10).fill(0);
  let rms = 0;
  let used = pts;
  if (pts.length < 8) return { cx, cy, rms, n: 0 };
  const nTerms = pts.length >= 40 ? 10 : pts.length >= 16 ? 6 : 3;
  for (let iter = 0; iter < 4; iter++) {
    const rows = used.map((p) => basis(p.x, p.y, tpl).slice(0, nTerms));
    const ws = used.map((p) => p.w);
    cx = fitRidge(rows, used.map((p) => p.dx), ws, 0.5);
    cy = fitRidge(rows, used.map((p) => p.dy), ws, 0.5);
    const res = pts.map((p) => {
      const b = basis(p.x, p.y, tpl).slice(0, nTerms);
      return Math.hypot(p.dx - dot(b, cx), p.dy - dot(b, cy));
    });
    const lim = Math.max(0.45, 2.5 * percentile(res, 50));
    const next = pts.filter((_, k) => res[k] <= lim);
    rms = Math.sqrt(next.reduce((s, p) => {
      const b = basis(p.x, p.y, tpl).slice(0, nTerms);
      return s + (p.dx - dot(b, cx)) ** 2 + (p.dy - dot(b, cy)) ** 2;
    }, 0) / Math.max(1, next.length));
    if (next.length === used.length || next.length < 8) break;
    used = next;
  }
  const pad = (c: number[]) => [...c, ...new Array(10 - c.length).fill(0)];
  return { cx: pad(cx), cy: pad(cy), rms, n: used.length };
}

export function alignItems(g: Gray, H: Homography, tpl: Template): AlignResult {
  const whites = {} as Record<Field, number[]>;
  for (const f of FIELDS) {
    const fd = tpl.fields[f];
    whites[f] = fd.items.map((item) => itemWhite(g, H, item.cells, fd.r));
  }
  const clamp = (v: number) => Math.max(-MAX_SHIFT, Math.min(MAX_SHIFT, v));

  // Dua putaran: (1) cari pusat tinta di sekitar posisi homography; (2) cari ulang di sekitar prediksi
  // medan putaran pertama — supaya pergeseran besar di tengah lembar tetap tertangkap tanpa memperlebar
  // jendela pencarian (yang berisiko terkunci ke bubble tetangga).
  let field = { cx: new Array(10).fill(0), cy: new Array(10).fill(0), rms: 0, n: 0 };
  for (let pass = 0; pass < 2; pass++) {
    const pts: Anchor[] = [];
    for (const f of FIELDS) {
      const fd = tpl.fields[f];
      fd.items.forEach((item, i) => {
        const white = whites[f][i];
        if (!(white > 0)) return;
        const [mx, my] = itemCenter(item.cells);
        const b = basis(mx, my, tpl);
        const px = clamp(dot(b, field.cx));
        const py = clamp(dot(b, field.cy));
        const lim = pass === 0 ? SEARCH : Math.min(SEARCH, Math.max(1.0, 3 * field.rms + 0.6));
        const fine = searchOffset(g, H, item.cells, fd.r, white, px, py, lim);
        // acuan hanya bila ada arsiran jelas & tunggal (bukan kosong / ganda)
        if (fine.dark >= 0.3 && fine.dark - fine.second >= 0.18)
          pts.push({ x: mx, y: my, dx: fine.dx, dy: fine.dy, w: Math.min(1, fine.dark) });
      });
    }
    const next = fitField(pts, tpl);
    if (next.n === 0) break;
    field = next;
  }

  // geseran akhir per nomor: medan + koreksi lokal kecil ke pusat tinta
  const offsets = {} as ItemOffsets;
  for (const f of FIELDS) {
    const fd = tpl.fields[f];
    offsets[f] = fd.items.map((item, i) => {
      const [mx, my] = itemCenter(item.cells);
      const b = basis(mx, my, tpl);
      let dx = clamp(dot(b, field.cx));
      let dy = clamp(dot(b, field.cy));
      const white = whites[f][i];
      if (white > 0) {
        const loc = searchOffset(g, H, item.cells, fd.r, white, dx, dy, REFINE);
        // pindah hanya bila memang menambah kegelapan arsiran (bukan sekadar derau)
        if (loc.dark > loc.base + 0.03 && loc.dark >= 0.2) {
          dx = loc.dx;
          dy = loc.dy;
        }
      }
      return [Math.round(dx * 100) / 100, Math.round(dy * 100) / 100] as [number, number];
    });
  }
  return { offsets, anchors: field.n, rms: Math.round(field.rms * 100) / 100 };
}

export const ZERO_OFFSETS = (tpl: Template): ItemOffsets =>
  Object.fromEntries(FIELDS.map((f) => [f, tpl.fields[f].items.map(() => [0, 0] as [number, number])])) as ItemOffsets;
