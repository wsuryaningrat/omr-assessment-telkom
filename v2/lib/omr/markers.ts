import { findBlobs, type Blob } from "./components";
import { computeHomography, applyH } from "./homography";
import { adaptiveThreshold, bilinear, otsu, percentile } from "./image";
import type { Corners, Gray, Homography, Pt, SheetDetection, Template } from "./types";

const ORDER = ["tl", "tr", "br", "bl"] as const;

/** Rata-rata intensitas pada grid titik di persegi panjang template (mm). */
export function sampleRectMean(g: Gray, H: Homography, x: number, y: number, w: number, h: number, nx = 7, ny = 5) {
  let s = 0;
  let n = 0;
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < ny; j++) {
      const p = applyH(H, x + ((i + 0.5) / nx) * w, y + ((j + 0.5) / ny) * h);
      const v = bilinear(g, p.x, p.y);
      if (!Number.isNaN(v)) {
        s += v;
        n++;
      }
    }
  }
  return n ? s / n : NaN;
}

function samplePointsMean(g: Gray, H: Homography, pts: [number, number][]) {
  const vals: number[] = [];
  for (const [x, y] of pts) {
    const p = applyH(H, x, y);
    const v = bilinear(g, p.x, p.y);
    if (!Number.isNaN(v)) vals.push(v);
  }
  return vals;
}

/** Skor gelap bar orientasi relatif terhadap kertas putih di atas & bawahnya. */
export function orientationScore(g: Gray, H: Homography, tpl: Template): number {
  const o = tpl.orientation;
  const bar = sampleRectMean(g, H, o.x + 1, o.y + 1, o.w - 2, o.h - 2, 9, 3);
  const refPts: [number, number][] = [];
  for (let i = 0; i <= 10; i++) {
    const x = o.x + (i / 10) * o.w;
    refPts.push([x, o.y - 1.5], [x, o.y + o.h + 1.8]);
  }
  const ref = percentile(samplePointsMean(g, H, refPts), 75);
  if (!(ref > 0) || Number.isNaN(bar)) return 0;
  return Math.max(0, Math.min(1, 1 - bar / ref));
}

/** Verifikasi tiap kotak hitam: tengah gelap, sekelilingnya terang. */
export function markerContrast(g: Gray, H: Homography, tpl: Template): number[] {
  const half = tpl.marker.size / 2;
  return ORDER.map((k) => {
    const [cx, cy] = tpl.marker.centers[k];
    const inner = sampleRectMean(g, H, cx - half + 1.5, cy - half + 1.5, 2 * half - 3, 2 * half - 3, 5, 5);
    const ring: [number, number][] = [];
    const rr = half + 2.2;
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * Math.PI * 2;
      ring.push([cx + Math.cos(a) * rr, cy + Math.sin(a) * rr]);
    }
    const ref = percentile(samplePointsMean(g, H, ring), 60);
    if (!(ref > 0) || Number.isNaN(inner)) return 0;
    return Math.max(0, 1 - inner / ref);
  });
}

function templatePts(tpl: Template): Pt[] {
  return ORDER.map((k) => ({ x: tpl.marker.centers[k][0], y: tpl.marker.centers[k][1] }));
}

function polyArea(p: Pt[]): number {
  let a = 0;
  for (let i = 0; i < p.length; i++) {
    const q = p[(i + 1) % p.length];
    a += p[i].x * q.y - q.x * p[i].y;
  }
  return a / 2;
}

function isConvex(p: Pt[]): boolean {
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const a = p[i];
    const b = p[(i + 1) % 4];
    const c = p[(i + 2) % 4];
    const cr = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
    if (Math.abs(cr) < 1e-9) return false;
    const s = Math.sign(cr);
    if (sign === 0) sign = s;
    else if (s !== sign) return false;
  }
  return true;
}

function pointInConvex(p: Pt, quad: Pt[]): boolean {
  let sign = 0;
  for (let i = 0; i < quad.length; i++) {
    const a = quad[i];
    const b = quad[(i + 1) % quad.length];
    const cr = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x);
    const sg = Math.sign(cr);
    if (sg === 0) continue;
    if (sign === 0) sign = sg;
    else if (sg !== sign) return false;
  }
  return true;
}

/**
 * Seberapa cocok homography dengan isi LJK: titik-titik yang di template pasti
 * kertas putih (jalur tepi kiri/kanan & celah antar-blok) harus seragam terang,
 * dan marker + bar orientasi harus jauh lebih gelap. 0..1, makin besar makin cocok.
 */
export function templateFit(g: Gray, H: Homography, tpl: Template): number {
  const white: [number, number][] = [];
  const { w: pw, h: ph } = tpl.page;
  const m = tpl.marker.centers;
  const edgeTop = m.tl[1] + tpl.marker.size / 2 + 6;
  const edgeBot = m.bl[1] - tpl.marker.size / 2 - 6;
  for (let i = 0; i <= 24; i++) {
    const y = edgeTop + ((edgeBot - edgeTop) * i) / 24;
    white.push([10.4, y], [pw - 10.4, y]);
  }
  for (const y of tpl.whiteRows ?? []) for (let i = 0; i <= 16; i++) white.push([16 + ((pw - 32) * i) / 16, y]);
  const wv = samplePointsMean(g, H, white);
  if (wv.length < white.length * 0.9) return 0; // sebagian jatuh di luar gambar
  const ref = percentile(wv, 90);
  if (!(ref > 0)) return 0;
  const uniform = wv.filter((v) => v > ref * 0.82).length / wv.length;
  const dark: number[] = [];
  const half = tpl.marker.size / 2 - 1.5;
  for (const k of ORDER) {
    const [cx, cy] = m[k];
    dark.push(...samplePointsMean(g, H, [[cx, cy], [cx - half, cy - half], [cx + half, cy - half], [cx - half, cy + half], [cx + half, cy + half]]));
  }
  const o = tpl.orientation;
  for (let i = 0; i < 8; i++) dark.push(...samplePointsMean(g, H, [[o.x + 2 + ((o.w - 4) * i) / 7, o.y + o.h / 2]]));
  const darkMean = dark.reduce((a, b) => a + b, 0) / Math.max(1, dark.length);
  const sep = Math.max(0, 1 - darkMean / ref);
  void ph;
  return uniform * sep;
}

function dist(a: Pt, b: Pt) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Sudut dalam segiempat (derajat). */
export function quadAngles(c: Corners): number[] {
  const p = [c.tl, c.tr, c.br, c.bl];
  return p.map((b, i) => {
    const a = p[(i + 3) % 4];
    const d = p[(i + 1) % 4];
    const v1 = { x: a.x - b.x, y: a.y - b.y };
    const v2 = { x: d.x - b.x, y: d.y - b.y };
    const cos = (v1.x * v2.x + v1.y * v2.y) / (Math.hypot(v1.x, v1.y) * Math.hypot(v2.x, v2.y));
    return (Math.acos(Math.max(-1, Math.min(1, cos))) * 180) / Math.PI;
  });
}

export interface DetectOptions {
  /** Batas luas segiempat minimum (fraksi frame). Default 0.05. */
  minAreaFrac?: number;
  /** Ambang skor bar orientasi. Default 0.35. */
  minOrientation?: number;
}

/**
 * Cari 4 kotak hitam pojok LJK di gambar (sebaiknya sudah di-downscale ke ~1000 px).
 * Mengembalikan pojok dalam koordinat gambar input + homography mm -> piksel.
 */
export function detectSheet(g: Gray, tpl: Template, opts: DetectOptions = {}): SheetDetection {
  const minSide = Math.min(g.w, g.h);
  const win = Math.max(15, Math.round(minSide / 7)) | 1;
  const bin = adaptiveThreshold(g, win, 0.22);
  const minA = Math.max(30, (0.008 * minSide) ** 2);
  const maxA = (0.11 * minSide) ** 2;
  const blobs = findBlobs(bin, g.w, g.h, minA, maxA);

  const cands = blobs.filter((b) => {
    const bw = b.maxX - b.minX + 1;
    const bh = b.maxY - b.minY + 1;
    const fill = b.area / (bw * bh);
    const compact = (b.l1 + b.l2) / b.area; // persegi pejal ~0.167
    const elong = b.l1 / b.l2;
    return fill >= 0.45 && compact >= 0.13 && compact <= 0.215 && elong <= 2.6;
  });
  cands.sort((a, b) => b.area - a.area);
  const top = cands.slice(0, 14);
  if (top.length < 4) {
    return { ok: false, reason: "Kotak hitam pojok belum terlihat (kurang dari 4).", candidates: top.length };
  }

  // Rasio luas kotak marker terhadap luas segiempat pusat-pusat marker (ideal ~0.00204).
  const tp = templatePts(tpl);
  const sheetArea = Math.abs(polyArea(tp));
  const idealRatio = (tpl.marker.size * tpl.marker.size) / sheetArea;
  const idealAspect =
    (dist(tp[1], tp[2]) + dist(tp[3], tp[0])) / (dist(tp[0], tp[1]) + dist(tp[2], tp[3]));
  const minAreaFrac = opts.minAreaFrac ?? 0.05;
  const frameArea = g.w * g.h;

  const combos: { pts: Pt[]; score: number; area: number }[] = [];
  const n = top.length;
  for (let a = 0; a < n; a++)
    for (let b = a + 1; b < n; b++)
      for (let c = b + 1; c < n; c++)
        for (let d = c + 1; d < n; d++) {
          const bs = [top[a], top[b], top[c], top[d]];
          const areas = bs.map((x) => x.area);
          const maxArea = Math.max(...areas);
          const minArea = Math.min(...areas);
          if (maxArea / minArea > 3.5) continue;
          const mx = bs.reduce((s, x) => s + x.cx, 0) / 4;
          const my = bs.reduce((s, x) => s + x.cy, 0) / 4;
          const ordered = [...bs].sort(
            (p, q) => Math.atan2(p.cy - my, p.cx - mx) - Math.atan2(q.cy - my, q.cx - mx),
          );
          const pts = ordered.map((x) => ({ x: x.cx, y: x.cy }));
          if (!isConvex(pts)) continue;
          const quad = Math.abs(polyArea(pts));
          if (quad < minAreaFrac * frameArea) continue;
          const meanA = areas.reduce((s, x) => s + x, 0) / 4;
          const ratio = meanA / quad;
          if (ratio < idealRatio * 0.35 || ratio > idealRatio * 3) continue;
          const s01 = dist(pts[0], pts[1]);
          const s12 = dist(pts[1], pts[2]);
          const s23 = dist(pts[2], pts[3]);
          const s30 = dist(pts[3], pts[0]);
          if (Math.max(s01, s23) / Math.min(s01, s23) > 1.7) continue;
          if (Math.max(s12, s30) / Math.min(s12, s30) > 1.7) continue;
          const asp = Math.max(s01 + s23, s12 + s30) / Math.min(s01 + s23, s12 + s30);
          if (asp < idealAspect * 0.72 || asp > idealAspect * 1.35) continue;
          // Di dalam area 4 marker tidak boleh ada kotak pejal lain seukuran marker
          // (benda hitam di luar kertas yang ikut terpilih biasanya memicu ini).
          let intruder = false;
          for (const other of top) {
            if (bs.includes(other)) continue;
            if (other.area < meanA * 0.45 || other.area > meanA * 2.2) continue;
            if (pointInConvex({ x: other.cx, y: other.cy }, pts)) {
              intruder = true;
              break;
            }
          }
          if (intruder) continue;
          const score =
            quad *
            Math.exp(-Math.abs(Math.log(ratio / idealRatio))) *
            Math.exp(-Math.abs(Math.log(asp / idealAspect)) * 2) *
            (minArea / maxArea) ** 0.5;
          combos.push({ pts, score, area: quad });
        }

  if (combos.length === 0) {
    return { ok: false, reason: "4 kotak hitam tidak membentuk lembar LJK yang utuh.", candidates: top.length };
  }
  combos.sort((a, b) => b.score - a.score);

  // Verifikasi kandidat teratas: orientasi (bar hitam atas) + kontras tiap kotak.
  const minOs = opts.minOrientation ?? 0.35;
  let lastReason = "Orientasi lembar tidak terbaca (bar hitam di atas tertutup?).";
  let lastOs = 0;
  let winner: SheetDetection | null = null;
  let winnerFit = -1;
  for (const cand of combos.slice(0, 25)) {
    let bestRot = -1;
    let bestOs = -1;
    let secondOs = -1;
    let bestH: Homography | null = null;
    for (let r = 0; r < 4; r++) {
      const dst = [0, 1, 2, 3].map((i) => cand.pts[(i + r) % 4]);
      const H = computeHomography(tp, dst);
      if (!H) continue;
      const os = orientationScore(g, H, tpl);
      if (os > bestOs) {
        secondOs = bestOs;
        bestOs = os;
        bestRot = r;
        bestH = H;
      } else if (os > secondOs) secondOs = os;
    }
    lastOs = Math.max(lastOs, bestOs);
    if (!bestH || bestOs < minOs || secondOs > bestOs * 0.7) continue;
    const contrast = markerContrast(g, bestH, tpl);
    if (Math.min(...contrast) < 0.3) {
      lastReason = "Salah satu kotak hitam pojok tertutup atau terlalu pucat.";
      continue;
    }
    const fit = templateFit(g, bestH, tpl);
    if (fit < 0.25) {
      lastReason = "Posisi 4 kotak hitam tidak cocok dengan lembar LJK v2.";
      continue;
    }
    if (fit > winnerFit) {
      const dst = [0, 1, 2, 3].map((i) => cand.pts[(i + bestRot) % 4]);
      winnerFit = fit;
      winner = {
        ok: true,
        corners: { tl: dst[0], tr: dst[1], br: dst[2], bl: dst[3] },
        H: bestH,
        orientationScore: bestOs,
        rotation: bestRot,
        areaFrac: cand.area / frameArea,
        candidates: top.length,
        fit,
      };
    }
  }
  if (winner) return winner;
  return { ok: false, reason: lastReason, orientationScore: lastOs, candidates: top.length };
}

/**
 * Perhalus posisi pusat kotak hitam pada gambar resolusi penuh (Otsu lokal + centroid).
 * `approx` dalam koordinat gambar penuh, `sidePx` perkiraan sisi marker (px).
 */
export function refineCorners(g: Gray, approx: Corners, sidePx: number): Corners {
  const out = {} as Corners;
  for (const k of ORDER) {
    const p = approx[k];
    const R = Math.round(sidePx * 1.1);
    const x0 = Math.max(0, Math.round(p.x - R));
    const y0 = Math.max(0, Math.round(p.y - R));
    const x1 = Math.min(g.w - 1, Math.round(p.x + R));
    const y1 = Math.min(g.h - 1, Math.round(p.y + R));
    const vals: number[] = [];
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) vals.push(g.data[y * g.w + x]);
    if (vals.length < 16) {
      out[k] = p;
      continue;
    }
    const thr = otsu(vals);
    // komponen gelap yang memuat / terdekat dengan titik awal
    const w = x1 - x0 + 1;
    const h = y1 - y0 + 1;
    const bin = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) bin[y * w + x] = g.data[(y0 + y) * g.w + x0 + x] <= thr ? 1 : 0;
    const blobs = findBlobs(bin, w, h, Math.max(4, (sidePx * 0.4) ** 2), (sidePx * 2) ** 2);
    let bestB: Blob | null = null;
    let bestD = Infinity;
    for (const b of blobs) {
      const d = Math.hypot(b.cx + x0 - p.x, b.cy + y0 - p.y);
      if (d < bestD) {
        bestD = d;
        bestB = b;
      }
    }
    out[k] = bestB && bestD < sidePx * 0.6 ? { x: bestB.cx + x0, y: bestB.cy + y0 } : p;
  }
  return out;
}

export function cornersToH(tpl: Template, c: Corners): Homography | null {
  return computeHomography(templatePts(tpl), ORDER.map((k) => c[k]));
}

export function scaleCorners(c: Corners, s: number): Corners {
  return {
    tl: { x: c.tl.x * s, y: c.tl.y * s },
    tr: { x: c.tr.x * s, y: c.tr.y * s },
    br: { x: c.br.x * s, y: c.br.y * s },
    bl: { x: c.bl.x * s, y: c.bl.y * s },
  };
}
