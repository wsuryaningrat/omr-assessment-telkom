import { percentile } from "./image";
import { FIELD_NAMES, type RawRead } from "./read";
import type { DecodedSheet, ItemResult, ItemStatus, Template } from "./types";

/**
 * Ubah kegelapan mentah menjadi jawaban.
 *
 * Kalibrasi per lembar: baseline = median kegelapan semua bubble (mayoritas kosong),
 * peak = persentil 97 (arsiran terkuat). Ambang mengikuti rentang ini sehingga
 * tahan terhadap foto gelap/terang, fotokopi pucat, atau pensil tipis.
 * Keputusan per nomor bersifat RELATIF: opsi tergelap dipilih jika cukup jauh dari
 * runner-up; jika tidak, ditandai "ragu" / "multi" untuk divalidasi manusia.
 *
 *
 * Posisi bubble sudah diselaraskan lokal (align.ts) sebelum dibaca, sehingga arsiran pekat terbaca
 * penuh walau kertas melengkung. Aturan cakupan 75% dihapus: di foto nyata ia membuat arsiran pekat
 * yang sedikit keluar/meleset dari garis bulatan sering jatuh ke "ragu".
 */
export function decodeSheet(raw: RawRead, tpl: Template): DecodedSheet {
  const all: number[] = [];
  for (const name of FIELD_NAMES) for (const item of raw[name]) for (const v of item) if (!Number.isNaN(v)) all.push(v);
  const baseline = percentile(all, 50);
  const peak = percentile(all, 97);
  const range = Math.max(peak - baseline, 0.15);
  const threshold = Math.max(0.1, 0.33 * range);
  const decodeItem = (scoresRaw: number[], options: string[], id: string, no?: number): ItemResult => {
    const scores = scoresRaw.map((v) => (Number.isNaN(v) ? 0 : Math.max(0, v - baseline)));
    const idx = scores.map((_, i) => i).sort((a, b) => scores[b] - scores[a]);
    const s1 = scores[idx[0]];
    const s2 = idx.length > 1 ? scores[idx[1]] : 0;
    let status: ItemStatus = "ok";
    let value: string | null = options[idx[0]];
    if (s1 < threshold) {
      value = null;
      status = s1 >= 0.7 * threshold ? "ragu" : "blank";
    } else if (s2 >= threshold && s2 >= 0.65 * s1) {
      value = null;
      status = "multi";
    } else if (s1 >= 1.5 * threshold && s2 < 0.45 * s1) {
      // arsiran pekat & jelas paling gelap: yakin (bekas hapusan tipis di opsi lain diabaikan)
      status = "ok";
    } else if (s1 - s2 < 0.5 * threshold || s2 >= 0.5 * s1) {
      status = "ragu";
    }
    const conf = Math.max(0, Math.min(1, (s1 - s2) / range));
    return { id, no, value, status, scores: scores.map((s) => Math.round(s * 1000) / 1000), conf: Math.round(conf * 100) / 100 };
  };

  const f = tpl.fields;
  const nimItems = raw.nim.map((sc, i) => decodeItem(sc, f.nim.options, f.nim.items[i].id, i + 1));
  const nimValue = nimItems.map((it) => (it.value !== null && it.status !== "multi" ? it.value : "?")).join("");
  const kodeItems = raw.kode_soal.map((sc, i) => decodeItem(sc, f.kode_soal.options, f.kode_soal.items[i].id, i + 1));
  return {
    nim: { value: nimValue, items: nimItems },
    kode_soal: combineDigits(kodeItems, "kode"),
    kode_items: kodeItems,
    kuisioner: raw.kuisioner.map((sc, i) => decodeItem(sc, f.kuisioner.options, f.kuisioner.items[i].id, i + 1)),
    soal: raw.soal.map((sc, i) => decodeItem(sc, f.soal.options, f.soal.items[i].id, i + 1)),
    stats: {
      baseline: Math.round(baseline * 1000) / 1000,
      peak: Math.round(peak * 1000) / 1000,
      threshold: Math.round(threshold * 1000) / 1000,
    },
  };
}

const SEVERITY: Record<ItemStatus, number> = { ok: 0, ragu: 1, blank: 2, multi: 3 };

/** Gabungkan digit (mis. kode soal) menjadi satu nilai; null jika ada digit kosong/ganda. */
export function combineDigits(items: ItemResult[], id: string): ItemResult {
  const worst = items.reduce<ItemStatus>((w, it) => (SEVERITY[it.status] > SEVERITY[w] ? it.status : w), "ok");
  const allBlank = items.every((it) => it.status === "blank");
  const complete = items.every((it) => it.value !== null && it.status !== "multi");
  return {
    id,
    value: complete ? items.map((it) => it.value).join("") : null,
    status: allBlank ? "blank" : complete ? (worst === "ragu" ? "ragu" : "ok") : worst === "blank" ? "ragu" : worst,
    scores: [],
    conf: Math.min(...items.map((it) => it.conf)),
  };
}

/** Jumlah item yang perlu perhatian validator. */
export function countFlags(d: DecodedSheet) {
  const items = [...d.nim.items, ...d.kode_items, ...d.kuisioner, ...d.soal];
  return {
    ragu: items.filter((i) => i.status === "ragu").length,
    multi: items.filter((i) => i.status === "multi").length,
    blankSoal: d.soal.filter((i) => i.status === "blank").length,
  };
}
