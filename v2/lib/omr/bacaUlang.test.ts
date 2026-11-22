import fs from "node:fs";
import path from "node:path";
import jpeg from "jpeg-js";
import { describe, expect, it } from "vitest";
import { gabungBacaUlang } from "./bacaUlang";
import { decodeSheet } from "./decode";
import { rgbaToGray } from "./image";
import { readSheetFull } from "./read";
import { scanRgba, TEMPLATE } from "./scan";

describe("baca ulang LJK lama dengan pembaca baru", () => {
  it("arsiran tipis yang dulu ragu jadi terisi; jawaban lama tidak berubah", () => {
    const FIX = path.join(__dirname, "__fixtures__");
    const truth = JSON.parse(fs.readFileSync(path.join(FIX, "truth.json"), "utf8"))["22_pensil_tipis"];
    const img = jpeg.decode(fs.readFileSync(path.join(FIX, "22_pensil_tipis.jpg")), { useTArray: true, maxMemoryUsageInMB: 1024 });
    const res = scanRgba(img.data, img.width, img.height);
    expect(res.ok).toBe(true);
    const { gray } = rgbaToGray(img.data, img.width, img.height);
    const { raw } = readSheetFull(gray, res.H!, TEMPLATE, res.offsets);
    const lama = decodeSheet(raw, TEMPLATE); // perilaku pembaca lama (tanpa penyelamatan arsiran tipis)
    const val = (xs: { value: string | null; status: string }[]) => xs.map((x) => (x.status === "ok" ? x.value : null));
    const flagsLama = [...lama.kuisioner, ...lama.soal].filter((x) => x.status === "ragu" || x.status === "multi").length;
    const stored = { flags: flagsLama, jawaban: val(lama.soal), kuisioner: val(lama.kuisioner), omr: { kuisioner: lama.kuisioner, soal: lama.soal } };
    // validator sempat mengoreksi nomor 75 secara manual -> harus dipertahankan
    stored.jawaban[74] = "D";
    const g = gabungBacaUlang(stored, res.decoded!);
    expect(flagsLama).toBeGreaterThan(30);
    expect(g.terisi).toBeGreaterThan(30);
    expect(g.flags).toBeLessThanOrEqual(5);
    expect(g.jawaban[74]).toBe("D");
    // tidak ada jawaban terisi yang salah
    g.jawaban.forEach((v, i) => {
      if (v !== null && i !== 74) expect(v).toBe(truth.soal[i]);
    });
  });
});
