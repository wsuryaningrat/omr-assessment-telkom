/** Statistik blob gelap hasil connected-components. */
export interface Blob {
  area: number;
  cx: number;
  cy: number;
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  /** Eigenvalue kovarians (λ1 >= λ2). */
  l1: number;
  l2: number;
}

/**
 * Connected components 8-tetangga pada citra biner (1 = gelap).
 * Hanya blob dengan luas [minArea, maxArea] yang dikembalikan.
 */
export function findBlobs(bin: Uint8Array, w: number, h: number, minArea: number, maxArea: number): Blob[] {
  const visited = new Uint8Array(w * h);
  const stack = new Int32Array(w * h);
  const blobs: Blob[] = [];
  for (let start = 0; start < w * h; start++) {
    if (!bin[start] || visited[start]) continue;
    let sp = 0;
    stack[sp++] = start;
    visited[start] = 1;
    let area = 0;
    let sx = 0;
    let sy = 0;
    let sxx = 0;
    let syy = 0;
    let sxy = 0;
    let minX = w;
    let minY = h;
    let maxX = 0;
    let maxY = 0;
    let tooBig = false;
    while (sp > 0) {
      const p = stack[--sp];
      const x = p % w;
      const y = (p - x) / w;
      area++;
      if (area > maxArea) tooBig = true;
      if (!tooBig) {
        sx += x;
        sy += y;
        sxx += x * x;
        syy += y * y;
        sxy += x * y;
      }
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      for (let dy = -1; dy <= 1; dy++) {
        const yy = y + dy;
        if (yy < 0 || yy >= h) continue;
        for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx;
          if (xx < 0 || xx >= w) continue;
          const q = yy * w + xx;
          if (bin[q] && !visited[q]) {
            visited[q] = 1;
            stack[sp++] = q;
          }
        }
      }
    }
    if (tooBig || area < minArea) continue;
    const cx = sx / area;
    const cy = sy / area;
    const vxx = sxx / area - cx * cx;
    const vyy = syy / area - cy * cy;
    const vxy = sxy / area - cx * cy;
    const tr = vxx + vyy;
    const det = vxx * vyy - vxy * vxy;
    const disc = Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
    blobs.push({ area, cx, cy, minX, minY, maxX, maxY, l1: tr / 2 + disc, l2: Math.max(1e-9, tr / 2 - disc) });
  }
  return blobs;
}
