import type { StoredItem, StoredOmr } from "@/lib/types";
import type { DecodedSheet } from "./types";
import { withWajib } from "./wajib";

const flagged = (items: { status: string }[]) => items.filter((i) => i.status === "ragu" || i.status === "multi").length;

export interface BacaUlangInput {
  flags: number;
  jawaban: (string | null)[];
  kuisioner: (string | null)[];
  omr: Pick<StoredOmr, "kuisioner" | "soal">;
}

/**
 * Gabungkan hasil baca ulang foto dengan data LJK yang tersimpan.
 * - Jawaban yang sudah terisi (hasil baca lama atau koreksi validator) TIDAK diubah.
 * - Nomor yang kosong diisi bila pembacaan baru yakin ("ok").
 * - Tanda baru = nomor yang masih ragu/ganda dan tetap kosong + bagian tanda NIM/kode soal yang lama.
 */
export function gabungBacaUlang(r: BacaUlangInput, d: Pick<DecodedSheet, "kuisioner" | "soal">) {
  let terisi = 0;
  const merge = (cur: (string | null)[], fresh: { value: string | null; status: string }[]) =>
    fresh.map((it, i) => {
      if (cur[i] != null) return cur[i];
      if (it.status === "ok" && it.value) {
        terisi++;
        return it.value;
      }
      return null;
    });
  const jawaban = merge(r.jawaban ?? [], d.soal);
  const kuisioner = merge(r.kuisioner ?? [], d.kuisioner);
  const sisa = (vals: (string | null)[], fresh: { status: string }[]) =>
    fresh.filter((it, i) => (it.status === "ragu" || it.status === "multi") && vals[i] == null).length;
  const lamaItem =
    flagged(withWajib(r.omr.kuisioner as StoredItem[], "kuisioner")) + flagged(withWajib(r.omr.soal as StoredItem[], "soal"));
  const nimKode = Math.max(0, r.flags - lamaItem);
  const flags = sisa(kuisioner, d.kuisioner) + sisa(jawaban, d.soal) + nimKode;
  return { jawaban, kuisioner, flags, terisi };
}
