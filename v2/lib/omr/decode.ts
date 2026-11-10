import { percentile } from "./image";
import { FIELD_NAMES, PIX_LEVELS, type RawCover, type RawRead } from "./read";
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
 * Bila `cover` tersedia, keterpilihan juga mensyaratkan CAKUPAN: proporsi area bulatan yang
 * tertutup tinta harus >= COVER_MIN (75%). Tingkat "tinta" per piksel diambil adaptif per
 * lembar (setengah dari kegelapan arsiran khas) supaya pensil tipis / fotokopi pucat tetap adil.
 */
export const COVER_MIN = 0.75;
/** Cakupan di bawah ini dianggap tidak diarsir sama sekali (bukan "ragu"). */
export const COVER_PARTIAL = 0.35;

export function decodeSheet(raw: RawRead, tpl: Template, cover?: RawCover): DecodedSheet {
  const all: number[] = [];
  for (const name of FIELD_NAMES) for (const item of raw[name]) for (const v of item) if (!Number.isNaN(v)) all.push(v);
  const baseline = percentile(all, 50);
  const peak = percentile(all, 97);
  const range = Math.max(peak - baseline, 0.15);
  const threshold = Math.max(0.1, 0.33 * range);
  // kegelapan arsiran khas = peak; piksel dihitung "tertutup" bila kegelapannya >= separuhnya
  const pixLevel = Math.min(0.5, Math.max(0.12, 0.5 * peak));
  let lk = 0;
  for (let k = 0; k < PIX_LEVELS.length; k++) if (PIX_LEVELS[k] <= pixLevel + 1e-9) lk = k;

  const decodeItem = (scoresRaw: number[], options: string[], id: string, no?: number, cov?: number[][]): ItemResult => {
    if (cov) return decodeByCoverage(scoresRaw, cov.map((c) => c[lk] ?? 0), options, id, no);
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
    } else if (s1 - s2 < 0.5 * threshold || s2 >= 0.5 * s1) {
      status = "ragu";
    }
    const conf = Math.max(0, Math.min(1, (s1 - s2) / range));
    return { id, no, value, status, scores: scores.map((s) => Math.round(s * 1000) / 1000), conf: Math.round(conf * 100) / 100 };
  };

  const decodeByCoverage = (scoresRaw: number[], covs: number[], options: string[], id: string, no?: number): ItemResult => {
    const scores = scoresRaw.map((v) => (Number.isNaN(v) ? 0 : Math.max(0, v - baseline)));
    const idx = covs.map((_, i) => i).sort((a, b) => covs[b] - covs[a] || scores[b] - scores[a]);
    const c1 = covs[idx[0]];
    const c2 = idx.length > 1 ? covs[idx[1]] : 0;
    // terpilih = cakupan >= 75% DAN memang lebih gelap dari kosong (bukan bayangan rata)
    const full = idx.filter((i) => covs[i] >= COVER_MIN && scores[i] >= threshold);
    let status: ItemStatus;
    let value: string | null;
    if (full.length >= 2) {
      value = null;
      status = "multi";
    } else if (full.length === 1) {
      value = options[full[0]];
      // ada opsi lain yang ikut terarsir sebagian -> minta dicek
      status = idx.some((i) => i !== full[0] && covs[i] >= COVER_PARTIAL) ? "ragu" : "ok";
    } else if (c1 >= COVER_PARTIAL && scores[idx[0]] >= 0.5 * threshold) {
      // arsiran kurang penuh (< 75%): tidak dihitung, tapi ditandai untuk validator
      value = options[idx[0]];
      status = "ragu";
    } else {
      value = null;
      status = "blank";
    }
    const conf = Math.max(0, Math.min(1, value ? (Math.min(c1, 1) - c2) : 1 - c1));
    return {
      id,
      no,
      value,
      status,
      scores: scores.map((s) => Math.round(s * 1000) / 1000),
      cover: covs.map((c) => Math.round(c * 100) / 100),
      conf: Math.round(conf * 100) / 100,
    };
  };

  const f = tpl.fields;
  const nimItems = raw.nim.map((sc, i) => decodeItem(sc, f.nim.options, f.nim.items[i].id, i + 1, cover?.nim[i]));
  const nimValue = nimItems.map((it) => (it.value !== null && it.status !== "multi" ? it.value : "?")).join("");
  const kodeItems = raw.kode_soal.map((sc, i) => decodeItem(sc, f.kode_soal.options, f.kode_soal.items[i].id, i + 1, cover?.kode_soal[i]));
  return {
    nim: { value: nimValue, items: nimItems },
    kode_soal: combineDigits(kodeItems, "kode"),
    kode_items: kodeItems,
    kuisioner: raw.kuisioner.map((sc, i) => decodeItem(sc, f.kuisioner.options, f.kuisioner.items[i].id, i + 1, cover?.kuisioner[i])),
    soal: raw.soal.map((sc, i) => decodeItem(sc, f.soal.options, f.soal.items[i].id, i + 1, cover?.soal[i])),
    stats: {
      baseline: Math.round(baseline * 1000) / 1000,
      peak: Math.round(peak * 1000) / 1000,
      threshold: Math.round(threshold * 1000) / 1000,
      pixLevel: cover ? PIX_LEVELS[lk] : undefined,
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
