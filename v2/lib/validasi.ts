import type { SubmissionStatus } from "@/lib/types";

/** Tab status di daftar LJK per kelas; dipakai juga untuk urutan sebelumnya/berikutnya di detail. */
export const TABS: { key: string; label: string; statuses: SubmissionStatus[] }[] = [
  { key: "menunggu", label: "Perlu validasi", statuses: ["perlu_validasi"] },
  { key: "valid", label: "Valid", statuses: ["valid"] },
  { key: "ditolak", label: "Ditolak / diganti", statuses: ["ditolak", "diganti"] },
];

export const tabOfStatus = (st: SubmissionStatus) => TABS.find((t) => t.statuses.includes(st)) ?? TABS[0];
