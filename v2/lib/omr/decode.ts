import { percentile } from "./image";
import { FIELD_NAMES, type RawRead } from "./read";
import type { DecodedSheet, ItemResult, ItemStatus, Template } from "./types";
import { withWajib } from "./wajib";

/** Arsiran tipis diterima bila gelapnya >= max(LIGHT_MIN, LIGHT_SIGMA x derau kertas) di atas bubble kosong. */
const LIGHT_MIN = 0.055;
const LIGHT_SIGMA = 5;
/** Opsi lain dianggap bersih bila <= max(EMPTY_MAX, EMPTY_SIGMA x derau) atau <= LIGHT_SECOND_RATIO x opsi tergelap. */
const EMPTY_MAX = 0.035;
const EMPTY_SIGMA = 3;
const LIGHT_SECOND_RATIO = 0.35;
/** Selisih minimum opsi tergelap dan kedua, relatif terhadap ambang arsiran tipis. */
const LIGHT_GAP = 0.7;
/** Tekstur bulatan tipis minimal proporsi ini dari tekstur bulatan kosong di nomor yang sama. */
const LIGHT_TEX_RATIO = 0.65;
/** Lembar dianggap "gaya arsir tipis" bila ada >= LIGHT_STYLE_MIN kandidat tipis dan >= LIGHT_STYLE_FRAC dari semua tanda. */
const LIGHT_STYLE_MIN = 4;
const LIGHT_STYLE_FRAC = 0.08;
/** Di luar gaya arsir tipis, tanda tipis tetap diterima bila gelapnya >= proporsi ini dari median arsiran tebal lembar itu. */
const LIGHT_REL_STRONG = 0.45;

interface Light {
  value: string;
  d1: number;
}

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
export function decodeSheet(raw: RawRead, tpl: Template, dark?: RawRead, tex?: RawRead): DecodedSheet {
  const all: number[] = [];
  for (const name of FIELD_NAMES) for (const item of raw[name]) for (const v of item) if (!Number.isNaN(v)) all.push(v);
  const baseline = percentile(all, 50);
  const peak = percentile(all, 97);
  const range = Math.max(peak - baseline, 0.15);
  const threshold = Math.max(0.1, 0.33 * range);

  // Statistik kegelapan apa adanya (tanpa pembobotan hapusan) untuk menyelamatkan arsiran tipis.
  const allDark: number[] = [];
  if (dark) for (const name of FIELD_NAMES) for (const item of dark[name]) for (const v of item) if (!Number.isNaN(v)) allDark.push(v);
  const darkBase = allDark.length ? percentile(allDark, 50) : 0;
  // sebaran bubble kosong (robust, MAD): mayoritas bubble memang kosong
  const noise = allDark.length ? 1.4826 * percentile(allDark.map((v) => Math.abs(v - darkBase)), 50) : 0;
  const lightOn = Math.max(LIGHT_MIN, LIGHT_SIGMA * noise);
  const lightOff = Math.max(EMPTY_MAX, EMPTY_SIGMA * noise);

  const decodeItem = (scoresRaw: number[], options: string[], id: string, no?: number, darkRaw?: number[], texRaw?: number[]): ItemResult => {
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
    // Arsiran pensil TIPIS: goresan abu-abu tanpa piksel pekat dibobot turun seperti bekas hapusan,
    // sehingga jatuh ke "ragu"/"kosong" walau jelas satu-satunya tanda di nomor ini. Terima bila
    // opsi tergelap jelas di atas derau kertas dan SEMUA opsi lain bersih. Dua tanda samar di nomor yang
    // sama (arsiran tipis + bekas hapusan) tetap "ragu".
    // Keputusannya ditunda ke akhir (lihat applyLight) karena bergantung pada gaya arsir lembar ini.
    const dk = darkRaw ? darkRaw.map((v) => (Number.isNaN(v) ? 0 : Math.max(0, v - darkBase))) : null;
    let light: Light | undefined;
    if (dk && status === "ok" && value !== null) strong.push(dk[options.indexOf(value)]);
    if (dk && (status === "ragu" || status === "blank")) {
      const di = dk.map((_, i) => i).sort((a, b) => dk[b] - dk[a]);
      const d1 = dk[di[0]];
      const d2 = di.length > 1 ? dk[di[1]] : 0;
      // Goresan pensil bertekstur (berbintik/bergaris); bekas hapusan cenderung abu-abu rata yang justru
      // menurunkan kontras huruf cetak di dalam bulatan. Bandingkan dengan bulatan kosong di nomor yang sama.
      let texOk = true;
      if (texRaw) {
        const others = di.slice(1).map((i) => texRaw[i]).filter((v) => !Number.isNaN(v));
        const ref = others.length ? percentile(others, 50) : NaN;
        texOk = !(ref > 0) || texRaw[di[0]] >= LIGHT_TEX_RATIO * ref;
      }
      if (texOk && d1 >= lightOn && d2 <= Math.max(lightOff, LIGHT_SECOND_RATIO * d1) && d1 - d2 >= LIGHT_GAP * lightOn)
        light = { value: options[di[0]], d1 };
    }
    const conf = Math.max(0, Math.min(1, (s1 - s2) / range));
    const res: ItemResult = { id, no, value, status, scores: scores.map((s) => Math.round(s * 1000) / 1000), conf: Math.round(conf * 100) / 100 };
    if (light) lights.push({ res, light });
    return res;
  };
  const strong: number[] = [];
  const lights: { res: ItemResult; light: Light }[] = [];

  const f = tpl.fields;
  const nimItems = raw.nim.map((sc, i) => decodeItem(sc, f.nim.options, f.nim.items[i].id, i + 1, dark?.nim[i], tex?.nim[i]));
  const kodeItems = raw.kode_soal.map((sc, i) => decodeItem(sc, f.kode_soal.options, f.kode_soal.items[i].id, i + 1, dark?.kode_soal[i], tex?.kode_soal[i]));
  const kuisItems = raw.kuisioner.map((sc, i) => decodeItem(sc, f.kuisioner.options, f.kuisioner.items[i].id, i + 1, dark?.kuisioner[i], tex?.kuisioner[i]));
  const soalItems = raw.soal.map((sc, i) => decodeItem(sc, f.soal.options, f.soal.items[i].id, i + 1, dark?.soal[i], tex?.soal[i]));

  // Arsiran tipis yang berdiri sendiri diterima bila (a) mahasiswa ini memang sering mengarsir tipis, atau
  // (b) gelapnya setidaknya ~separuh arsiran tebalnya sendiri. Lembar yang arsirannya tebal semua dengan
  // satu-dua tanda samar → tanda samar itu lebih mungkin bekas hapusan, tetap "ragu".
  const medStrong = strong.length ? percentile(strong, 50) : 0;
  const lightStyle = lights.length >= LIGHT_STYLE_MIN && lights.length >= LIGHT_STYLE_FRAC * (lights.length + strong.length);
  let lightAccepted = 0;
  for (const { res, light } of lights) {
    if (lightStyle || (medStrong > 0 && light.d1 >= LIGHT_REL_STRONG * medStrong)) {
      res.status = "ok";
      res.value = light.value;
      lightAccepted++;
    }
  }
  const nimValue = nimItems.map((it) => (it.value !== null && it.status !== "multi" ? it.value : "?")).join("");
  return {
    nim: { value: nimValue, items: nimItems },
    kode_soal: combineDigits(kodeItems, "kode"),
    kode_items: kodeItems,
    // nomor wajib (kuisioner 1-8, soal 1-50) yang kosong -> "ragu" agar dicek validator
    kuisioner: withWajib(kuisItems, "kuisioner"),
    soal: withWajib(soalItems, "soal"),
    stats: {
      baseline: Math.round(baseline * 1000) / 1000,
      peak: Math.round(peak * 1000) / 1000,
      threshold: Math.round(threshold * 1000) / 1000,
      light: lightAccepted,
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
