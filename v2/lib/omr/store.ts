import type { StoredItem, StoredOmr } from "@/lib/types";
import type { ScanResult } from "./scan";
import type { ItemResult } from "./types";

const strip = (i: ItemResult): StoredItem => ({ value: i.value, status: i.status, scores: i.scores, conf: i.conf });

/** Ringkas hasil scan untuk disimpan di kolom `omr` (jsonb). */
export function toStoredOmr(scan: ScanResult, image: { w: number; h: number }): StoredOmr {
  const d = scan.decoded!;
  return {
    template_version: scan.templateVersion,
    image,
    corners: scan.corners!,
    quality: scan.quality!,
    stats: d.stats,
    nim: d.nim.items.map(strip),
    kode_soal: strip(d.kode_soal),
    kuisioner: d.kuisioner.map(strip),
    soal: d.soal.map(strip),
    ms: scan.ms,
    captured_at: new Date().toISOString(),
    user_agent: typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 200) : undefined,
  };
}

/** Jumlah item yang perlu diperhatikan validator. */
export function countOmrFlags(omr: StoredOmr, nimMatched: boolean): number {
  const items = [omr.kode_soal, ...omr.kuisioner, ...omr.soal];
  return items.filter((i) => i.status === "ragu" || i.status === "multi").length + (nimMatched ? 0 : 1) + (omr.kode_soal.value ? 0 : 1);
}

/** Digit NIM per kolom dari string (karakter '?' -> null). */
export function nimToDigits(nim: string, len = 12): (string | null)[] {
  return Array.from({ length: len }, (_, i) => (nim[i] && /\d/.test(nim[i]) ? nim[i] : null));
}
