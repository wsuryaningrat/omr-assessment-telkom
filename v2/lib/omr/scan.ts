import { decodeSheet } from "./decode";
import { scaleH } from "./homography";
import { downscaleGray, rgbaToGray } from "./image";
import { cornersToH, detectSheet, refineCorners, scaleCorners } from "./markers";
import { assessQuality } from "./quality";
import { alignItems, type ItemOffsets } from "./align";
import { readSheet, type RawRead } from "./read";
import templateJson from "./template.json";
import type { Corners, DecodedSheet, Gray, Homography, QualityReport, SheetDetection, Template } from "./types";

export const TEMPLATE = templateJson as unknown as Template;

/** Sisi terpanjang gambar untuk tahap deteksi kotak hitam. */
export const DETECT_MAX_SIDE = 960;

export interface ScanResult {
  ok: boolean;
  reason?: string;
  detection: SheetDetection;
  /** Pojok marker dalam koordinat gambar penuh. */
  corners?: Corners;
  H?: Homography;
  quality?: QualityReport;
  raw?: RawRead;
  /** Geseran lokal per nomor (mm) hasil penyelarasan — dipakai juga untuk overlay. */
  offsets?: ItemOffsets;
  alignAnchors?: number;
  alignRms?: number;
  decoded?: DecodedSheet;
  templateVersion: string;
  ms: number;
}

/** Deteksi cepat untuk frame kamera live (gambar kecil). */
export function detectFrame(gray: Gray, tpl: Template = TEMPLATE): SheetDetection {
  return detectSheet(gray, tpl);
}

/** Pipeline lengkap pada satu gambar grayscale resolusi penuh. */
export function scanGray(full: Gray, tpl: Template = TEMPLATE): ScanResult {
  const t0 = Date.now();
  const { gray: small, scale } = downscaleGray(full, DETECT_MAX_SIDE);
  const det = detectSheet(small, tpl);
  if (!det.ok || !det.corners || !det.H) {
    return { ok: false, reason: det.reason, detection: det, templateVersion: tpl.version, ms: Date.now() - t0 };
  }
  const approx = scaleCorners(det.corners, 1 / scale);
  const Happrox = scaleH(det.H, 1 / scale);
  const markerPx = tpl.marker.size * Math.sqrt(Math.abs(Happrox[0] * Happrox[4] - Happrox[1] * Happrox[3]));
  const corners = refineCorners(full, approx, Math.max(6, markerPx));
  const H = cornersToH(tpl, corners) ?? Happrox;
  const quality = assessQuality(full, H, corners, tpl);
  const align = alignItems(full, H, tpl);
  const raw = readSheet(full, H, tpl, align.offsets);
  const decoded = decodeSheet(raw, tpl);
  return {
    ok: true,
    detection: det,
    corners,
    H,
    quality,
    raw,
    decoded,
    offsets: align.offsets,
    alignAnchors: align.anchors,
    alignRms: align.rms,
    templateVersion: tpl.version,
    ms: Date.now() - t0,
  };
}

export function scanRgba(rgba: Uint8ClampedArray | Uint8Array, w: number, h: number, tpl: Template = TEMPLATE): ScanResult {
  const { gray } = rgbaToGray(rgba, w, h);
  return scanGray(gray, tpl);
}
