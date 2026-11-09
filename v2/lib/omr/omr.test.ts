import fs from "node:fs";
import path from "node:path";
import jpeg from "jpeg-js";
import { describe, expect, it } from "vitest";
import { scanRgba } from "./scan";
import { matchNim } from "./nim";
import { grade, parseKunciRows } from "./grade";

const FIX = path.join(__dirname, "__fixtures__");
const truth: Record<string, any> = JSON.parse(fs.readFileSync(path.join(FIX, "truth.json"), "utf8"));

function load(name: string) {
  const img = jpeg.decode(fs.readFileSync(path.join(FIX, `${name}.jpg`)), { useTArray: true, maxMemoryUsageInMB: 1024 });
  return img;
}

const rows: string[] = [];

describe("OMR LJK v2 - foto sintetis", () => {
  for (const name of Object.keys(truth)) {
    const t = truth[name];
    it(name, () => {
      const img = load(name);
      const res = scanRgba(img.data, img.width, img.height);
      if (t.expect_fail) {
        expect(res.ok).toBe(false);
        rows.push(`${name.padEnd(24)} gagal (benar) : ${res.reason}`);
        return;
      }
      expect(res.ok, res.reason).toBe(true);
      const d = res.decoded!;
      const nimOk = d.nim.value === t.nim;
      const kodeOk = d.kode_soal.value === t.kode_soal;
      let soalBenar = 0;
      let soalSalahBaca = 0;
      let soalDitandai = 0;
      t.soal.forEach((exp: string, i: number) => {
        const it = d.soal[i];
        if (exp === "MULTI") {
          if (it.status === "multi" || it.status === "ragu") soalDitandai++;
          else soalSalahBaca++;
          return;
        }
        const e = exp.replace("?", "") || null;
        if (it.value === e) soalBenar++;
        else if (it.status !== "ok") soalDitandai++;
        else soalSalahBaca++;
      });
      let kuisBenar = 0;
      let kuisSalah = 0;
      t.kuisioner.forEach((exp: string, i: number) => {
        const it = d.kuisioner[i];
        if (exp === "MULTI") {
          if (it.status === "ok") kuisSalah++;
          return;
        }
        const e = exp.replace("?", "") || null;
        if (it.value === e) kuisBenar++;
        else if (it.status === "ok") kuisSalah++;
      });
      const q = res.quality!;
      rows.push(
        `${name.padEnd(24)} nim:${nimOk ? "OK" : "X " + d.nim.value} kode:${kodeOk ? "OK" : "X"} soal ${soalBenar}/75 (salah-baca-tanpa-flag ${soalSalahBaca}, flag ${soalDitandai}) kuis ${kuisBenar}/15 | px/mm ${q.pxPerMm} sharp ${q.sharpness} ang ${q.minAngle}-${q.maxAngle} ${q.issues.join(";")} | ${res.ms}ms`,
      );
      // Syarat lulus: tidak boleh ada salah baca diam-diam untuk arsiran.
      if (t.style === "fill") {
        expect(nimOk).toBe(true);
        expect(kodeOk).toBe(true);
        expect(soalSalahBaca).toBe(0);
        expect(kuisSalah).toBe(0);
        expect(soalBenar).toBeGreaterThanOrEqual(72);
      } else if (t.style === "mixed") {
        expect(nimOk).toBe(true);
        expect(soalSalahBaca).toBe(0);
        expect(kuisSalah).toBe(0);
      } else {
        // silang: boleh ditandai ragu, tapi tidak boleh salah opsi dengan status ok
        expect(soalSalahBaca).toBe(0);
      }
    });
  }
  it("ringkasan", () => {
    fs.writeFileSync("/tmp/omr-summary.txt", rows.join("\n"));
  });
});

describe("util", () => {
  it("matchNim", () => {
    const roster = [
      { nim: "103012600002", nama: "A" },
      { nim: "103012600012", nama: "B" },
      { nim: "103052600001", nama: "C" },
    ];
    expect(matchNim("103012600002", roster).exact?.nama).toBe("A");
    const m = matchNim("1030126000?2", roster);
    expect(m.exact).toBeNull();
    expect(m.suggestions[0].nama).toBe("A");
  });
  it("grade + parseKunci", () => {
    const k = parseKunciRows([["No", "Kunci"], [1, "B"], [2, "A,C"], [3, "*"], [4, "D"]]);
    expect(k).toEqual({ 1: ["B"], 2: ["A", "C"], 3: ["A", "B", "C", "D"], 4: ["D"] });
    const g = grade(["B", "C", "A", null, "A"], k);
    expect(g).toMatchObject({ benar: 3, salah: 0, kosong: 1, total: 4, nilai: 75 });
  });
});
