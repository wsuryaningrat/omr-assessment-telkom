/** Kunci jawaban: nomor soal -> opsi yang diterima (["B"], ["A","C"], atau semua untuk bonus). */
export type Kunci = Record<number, string[]>;

export interface GradeResult {
  benar: number;
  salah: number;
  kosong: number;
  total: number;
  /** 0..100, dibulatkan 2 desimal. */
  nilai: number;
  detail: Record<number, "B" | "S" | "K">;
}

/**
 * Nilai jawaban terhadap kunci. Hanya nomor yang ada di kunci yang dihitung,
 * jadi LJK 75 slot tetap bisa dipakai untuk tes 50 soal.
 */
export function grade(answers: (string | null)[], kunci: Kunci): GradeResult {
  const nos = Object.keys(kunci)
    .map(Number)
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);
  let benar = 0;
  let salah = 0;
  let kosong = 0;
  const detail: GradeResult["detail"] = {};
  for (const no of nos) {
    const a = answers[no - 1];
    if (!a) {
      kosong++;
      detail[no] = "K";
    } else if (kunci[no].includes(a)) {
      benar++;
      detail[no] = "B";
    } else {
      salah++;
      detail[no] = "S";
    }
  }
  const total = nos.length;
  return { benar, salah, kosong, total, nilai: total ? Math.round((benar / total) * 10000) / 100 : 0, detail };
}

/**
 * Parse kunci dari baris tabel: [no, jawaban] atau satu kolom berurutan.
 * Mendukung "A,C" (lebih dari satu benar) dan "*" / "BONUS" (semua benar).
 */
export function parseKunciRows(rows: unknown[][]): Kunci {
  const out: Kunci = {};
  const clean = rows
    .map((r) => r.map((c) => (c === null || c === undefined ? "" : String(c).trim())).filter((c) => c !== ""))
    .filter((r) => r.length > 0);
  const numbered = clean.some((r) => r.length >= 2 && /^\d+$/.test(r[0]));
  let seq = 1;
  for (const r of clean) {
    let no: number;
    let v: string;
    if (numbered) {
      if (r.length < 2 || !/^\d+$/.test(r[0])) continue;
      no = Number(r[0]);
      v = r[1].toUpperCase();
    } else {
      v = r[0].toUpperCase();
      if (["NO", "NOMOR", "SOAL", "KUNCI", "KEY", "JAWABAN"].includes(v)) continue;
      no = seq++;
    }
    if (["*", "ALL", "SEMUA", "BONUS"].includes(v)) out[no] = ["A", "B", "C", "D"];
    else {
      const opts = v.match(/[A-D]/g);
      if (opts) out[no] = [...new Set(opts)];
    }
  }
  return out;
}
