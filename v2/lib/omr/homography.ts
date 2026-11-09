import type { Homography, Pt } from "./types";

/** Hitung homography dari 4 pasang titik (src -> dst) dengan DLT + eliminasi Gauss. */
export function computeHomography(src: Pt[], dst: Pt[]): Homography | null {
  if (src.length !== 4 || dst.length !== 4) return null;
  const A: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i++) {
    const { x, y } = src[i];
    const { x: u, y: v } = dst[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    b.push(v);
  }
  const h = solve(A, b);
  if (!h) return null;
  return [...h, 1];
}

function solve(A: number[][], b: number[]): number[] | null {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
    if (Math.abs(M[piv][c]) < 1e-12) return null;
    [M[c], M[piv]] = [M[piv], M[c]];
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      if (f === 0) continue;
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((row, i) => row[n] / M[i][i]);
}

export function applyH(H: Homography, x: number, y: number): Pt {
  const d = H[6] * x + H[7] * y + H[8];
  return { x: (H[0] * x + H[1] * y + H[2]) / d, y: (H[3] * x + H[4] * y + H[5]) / d };
}

/** Skala lokal (piksel per mm) di titik template (x,y). */
export function localScale(H: Homography, x: number, y: number): number {
  const p = applyH(H, x, y);
  const px = applyH(H, x + 1, y);
  const py = applyH(H, x, y + 1);
  const sx = Math.hypot(px.x - p.x, px.y - p.y);
  const sy = Math.hypot(py.x - p.x, py.y - p.y);
  return Math.sqrt(sx * sy);
}

/** Kalikan seluruh output homography dengan faktor s (mis. dari skala deteksi ke resolusi penuh). */
export function scaleH(H: Homography, s: number): Homography {
  return [H[0] * s, H[1] * s, H[2] * s, H[3] * s, H[4] * s, H[5] * s, H[6], H[7], H[8]];
}
