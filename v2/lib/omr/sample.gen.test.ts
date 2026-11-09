// Generator data contoh untuk /demo/validasi (dijalankan manual: GEN_SAMPLE=1 npx vitest run lib/omr/sample.gen.test.ts)
import fs from "node:fs";
import path from "node:path";
import jpeg from "jpeg-js";
import { it } from "vitest";
import { scanRgba } from "./scan";
import { toStoredOmr } from "./store";

it.runIf(process.env.GEN_SAMPLE === "1")("generate demo sample", () => {
  const file = path.join(__dirname, "../../public/ljk/contoh_foto_ljk_v2.jpg");
  const img = jpeg.decode(fs.readFileSync(file), { useTArray: true });
  const scan = scanRgba(img.data, img.width, img.height);
  if (!scan.ok) throw new Error(scan.reason);
  const omr = toStoredOmr(scan, { w: img.width, h: img.height });
  omr.user_agent = "demo";
  fs.writeFileSync(path.join(__dirname, "../../app/demo/validasi/sample.json"), JSON.stringify({ nim: scan.decoded!.nim.value, omr }));
});
