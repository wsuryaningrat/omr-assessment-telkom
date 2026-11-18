import type { ItemStatus } from "./types";

/**
 * Nomor yang wajib diisi peserta. Bila nomor ini terbaca KOSONG, statusnya dijadikan "ragu"
 * supaya ikut ditandai dan dicek validator (sering: arsiran terlalu tipis / terlewat dibaca).
 * Nomor di luar rentang ini (kuisioner 9-15, soal 51-75) boleh kosong.
 */
export const WAJIB_ISI = { kuisioner: 8, soal: 50 } as const;

/** Status efektif: kosong pada nomor wajib -> "ragu". Berlaku juga untuk LJK yang tersimpan sebelum aturan ini. */
export function withWajib<T extends { status: ItemStatus }>(items: T[], field: keyof typeof WAJIB_ISI): T[] {
  const n = WAJIB_ISI[field];
  return items.map((it, i) => (i < n && it.status === "blank" ? { ...it, status: "ragu" as ItemStatus } : it));
}
