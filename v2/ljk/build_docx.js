/**
 * Bangun LJK v2 versi Word (.docx).
 *
 * Semua elemen yang dibaca OMR (4 kotak hitam, bar orientasi, bubble, bingkai) ada di
 * SATU gambar latar 600 dpi yang dikunci di posisi halaman (0,0) & ukuran A4 penuh,
 * di belakang teks. Teks yang aman diubah (judul, petunjuk, pernyataan, footer) dibuat
 * sebagai frame teks absolut di posisi yang sama dengan versi PDF.
 *
 *   python ljk/generate_ljk.py && node ljk/build_docx.js
 *   -> public/ljk/LJK_v2.docx
 */
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, AlignmentType, FrameAnchorType,
  HorizontalPositionRelativeFrom, VerticalPositionRelativeFrom, TextWrappingType, LineRuleType,
} = require("docx");

const HERE = __dirname;
const ROOT = path.dirname(HERE);
const BG = path.join(HERE, "build", "ljk_background_600dpi.png");
const BLOCKS = JSON.parse(fs.readFileSync(path.join(HERE, "build", "text_blocks.json"), "utf8"));
const OUT = path.join(ROOT, "public", "ljk", "LJK_v2.docx");

const MM_TWIP = 1440 / 25.4; // 1 mm dalam twip
const twip = (mm) => Math.round(mm * MM_TWIP);
const PAGE_W_MM = BLOCKS.page.w;
const PAGE_H_MM = BLOCKS.page.h;
// docx-js memakai piksel 96 dpi untuk ukuran gambar (1 px = 9525 EMU)
const mmToPx = (mm) => (mm / 25.4) * 96;

const children = [];

// 1) Gambar latar OMR, terkunci ke halaman, di belakang teks
children.push(
  new Paragraph({
    spacing: { before: 0, after: 0, line: 20, lineRule: LineRuleType.EXACT },
    children: [
      new ImageRun({
        type: "png",
        data: fs.readFileSync(BG),
        transformation: { width: mmToPx(PAGE_W_MM), height: mmToPx(PAGE_H_MM) },
        altText: { title: "LJK v2 - area OMR", description: "Jangan dipindah/diubah ukurannya: posisi kotak hitam & bulatan dibaca mesin.", name: "ljk-omr-background" },
        floating: {
          horizontalPosition: { relative: HorizontalPositionRelativeFrom.PAGE, offset: 0 },
          verticalPosition: { relative: VerticalPositionRelativeFrom.PAGE, offset: 0 },
          behindDocument: true,
          allowOverlap: true,
          lockAnchor: true,
          layoutInCell: false,
          wrap: { type: TextWrappingType.NONE },
        },
      }),
    ],
  }),
);

// 2) Teks yang bisa diedit, sebagai frame absolut
for (const blk of BLOCKS.blocks) {
  const [x, y, w, h] = blk.box;
  const lead = blk.leading ?? 1.18;
  const frame = {
    type: "absolute",
    position: { x: twip(x), y: twip(y) },
    width: twip(w),
    height: twip(h),
    rule: "exact",
    anchor: { horizontal: FrameAnchorType.PAGE, vertical: FrameAnchorType.PAGE },
  };
  // pusatkan vertikal blok judul seperti versi PDF (estimasi tinggi baris)
  let padTop = 0;
  if (blk.valign === "middle") {
    const total = blk.lines.reduce((s, ln) => s + ln.size * lead * 0.3528, 0);
    padTop = Math.max(0, (h - total) / 2);
  }
  blk.lines.forEach((ln, i) => {
    children.push(
      new Paragraph({
        frame,
        alignment: blk.align === "center" ? AlignmentType.CENTER : AlignmentType.LEFT,
        spacing: {
          before: i === 0 ? twip(padTop) : 0,
          after: 0,
          line: Math.round(ln.size * lead * 20),
          lineRule: LineRuleType.EXACT,
        },
        children: [
          new TextRun({
            text: ln.text,
            font: "Arial",
            size: Math.round(ln.size * 2),
            bold: !!ln.bold,
            italics: !!ln.italic,
            color: ln.bold ? "000000" : "333333",
          }),
        ],
      }),
    );
  });
}

const doc = new Document({
  creator: "Math Center CSWDA - Telkom University",
  title: "LJK v2 - Profiling Literasi Numerik",
  description:
    "Teks judul, petunjuk, pernyataan & footer boleh diedit. JANGAN memindah/mengubah ukuran gambar latar (kotak hitam & bulatan) dan cetak dengan skala 100% (Actual size).",
  styles: { default: { document: { run: { font: "Arial", size: 12 } } } },
  sections: [
    {
      properties: {
        page: {
          size: { width: twip(PAGE_W_MM), height: twip(PAGE_H_MM) },
          margin: { top: twip(4), bottom: twip(4), left: twip(4), right: twip(4), header: 0, footer: 0 },
        },
      },
      children,
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log("DOCX ->", OUT, (buf.length / 1024).toFixed(0) + " KB");
});
