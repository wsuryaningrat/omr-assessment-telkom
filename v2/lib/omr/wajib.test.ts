import { describe, expect, it } from "vitest";
import { WAJIB_ISI, withWajib } from "./wajib";

const item = (status: "ok" | "blank" | "ragu" | "multi") => ({ value: status === "ok" ? "A" : null, status });

describe("withWajib", () => {
  it("kosong pada soal 1-50 menjadi ragu, 51-75 tetap kosong", () => {
    const soal = Array.from({ length: 75 }, () => item("blank"));
    const out = withWajib(soal, "soal");
    expect(out.slice(0, WAJIB_ISI.soal).every((i) => i.status === "ragu")).toBe(true);
    expect(out.slice(WAJIB_ISI.soal).every((i) => i.status === "blank")).toBe(true);
  });
  it("kosong pada kuisioner 1-8 menjadi ragu; status lain tidak berubah", () => {
    const k = [item("ok"), item("multi"), ...Array.from({ length: 13 }, () => item("blank"))];
    const out = withWajib(k, "kuisioner");
    expect(out.map((i) => i.status).slice(0, 10)).toEqual(["ok", "multi", "ragu", "ragu", "ragu", "ragu", "ragu", "ragu", "blank", "blank"]);
    expect(k[2].status).toBe("blank"); // input tidak dimutasi
  });
});
