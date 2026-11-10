"""Generator LJK v2 + template koordinat OMR (umum, bisa dipakai ujian apa pun).

PDF dan JSON koordinat dibuat dari SATU sumber geometri di file ini, jadi posisi
bubble di template selalu persis sama dengan yang tercetak (tidak perlu kalibrasi
manual seperti v1).

Semua koordinat dalam milimeter, origin di pojok KIRI-ATAS halaman A4 (210 x 297).

Jalankan:
    python v2/ljk/generate_ljk.py
Output:
    v2/public/ljk/LJK_v2.pdf          template cetak lengkap
    v2/lib/omr/template.json          koordinat bubble untuk engine OMR
    v2/ljk/build/ljk_background.pdf   latar tanpa teks yang bisa diedit (untuk versi Word)
    v2/ljk/build/text_blocks.json     posisi & isi teks yang bisa diedit (untuk versi Word)
"""
import json
import os

from reportlab.lib.colors import Color, black, white
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PDF_OUT = os.path.join(ROOT, "public", "ljk", "LJK_v2.pdf")
JSON_OUT = os.path.join(ROOT, "lib", "omr", "template.json")
BUILD_DIR = os.path.join(HERE, "build")
BG_OUT = os.path.join(BUILD_DIR, "ljk_background.pdf")
BLOCKS_OUT = os.path.join(BUILD_DIR, "text_blocks.json")

TEMPLATE_VERSION = "ljk-v2-2"
PAGE_W, PAGE_H = 210.0, 297.0

# ---------------------------------------------------------------------------
# Geometri
# ---------------------------------------------------------------------------
MARKER_SIZE = 10.0
MARKERS = {  # pusat kotak hitam
    "tl": (14.0, 14.0),
    "tr": (196.0, 14.0),
    "br": (196.0, 283.0),
    "bl": (14.0, 283.0),
}
# Bar orientasi: hanya ada di ATAS tengah. Dipakai untuk membedakan LJK yang
# terbalik 180 derajat / landscape.
ORIENTATION = {"x": 85.0, "y": 11.0, "w": 40.0, "h": 6.0}

KODE_LEN = 3  # kode soal 3 digit, diarsir seperti NIM
N_SOAL = 75
N_KUIS = 15
NIM_LEN = 12

GRAY_LINE = Color(0.35, 0.35, 0.35)
GRAY_LABEL = Color(0.62, 0.62, 0.62)
GRAY_FRAME = Color(0.55, 0.55, 0.55)
GRAY_TEXT = Color(0.2, 0.2, 0.2)


KODE_X0 = 166.0
CONTOH_X = 191.6


def build_geometry():
    fields = {}

    # NIM: 12 kolom x 10 digit
    nim_r = 2.1
    nim_x0, nim_dx = 90.25, 5.5
    nim_y0, nim_dy = 58.0, 5.4
    nim_items = []
    for c in range(NIM_LEN):
        cells = [[round(nim_x0 + c * nim_dx, 3), round(nim_y0 + d * nim_dy, 3)] for d in range(10)]
        nim_items.append({"id": f"nim{c + 1}", "cells": cells})
    fields["nim"] = {
        "kind": "digits",
        "label": "NIM",
        "options": [str(d) for d in range(10)],
        "r": nim_r,
        "items": nim_items,
    }

    # Kode soal: 3 kolom x 10 digit, sejajar dengan grid NIM
    kode_items = []
    for c in range(KODE_LEN):
        cells = [[round(KODE_X0 + c * nim_dx, 3), round(nim_y0 + d * nim_dy, 3)] for d in range(10)]
        kode_items.append({"id": f"kode{c + 1}", "cells": cells})
    fields["kode_soal"] = {
        "kind": "digits",
        "label": "Kode Soal",
        "options": [str(d) for d in range(10)],
        "r": nim_r,
        "items": kode_items,
    }

    # Kuisioner: 15 item, 3 kolom x 5 baris
    kuis_r = 2.1
    kuis_items = []
    for i in range(N_KUIS):
        col, row = divmod(i, 5)
        x0 = 22.0 + col * 62.0
        y = 128.0 + row * 5.6
        cells = [[round(x0 + 9.0 + k * 6.0, 3), round(y, 3)] for k in range(4)]
        kuis_items.append({"id": f"k{i + 1}", "no": i + 1, "cells": cells, "label_xy": [x0 + 5.0, y]})
    fields["kuisioner"] = {
        "kind": "mcq",
        "label": "Kuisioner",
        "options": ["A", "B", "C", "D"],
        "r": kuis_r,
        "items": kuis_items,
    }

    # Soal: 75 item, 5 kolom x 15 baris (kelompok 5)
    soal_r = 2.3
    soal_items = []
    for i in range(N_SOAL):
        col, row = divmod(i, 15)
        x0 = 14.0 + col * 37.0
        y = 170.0 + row * 6.2 + (row // 5) * 3.0
        cells = [[round(x0 + 12.0 + k * 6.2, 3), round(y, 3)] for k in range(4)]
        soal_items.append({"id": f"s{i + 1}", "no": i + 1, "cells": cells, "label_xy": [x0 + 7.5, y]})
    fields["soal"] = {
        "kind": "mcq",
        "label": "Jawaban Soal",
        "options": ["A", "B", "C", "D"],
        "r": soal_r,
        "items": soal_items,
    }
    return fields


# ---------------------------------------------------------------------------
# Logo di pita atas, di antara kotak hitam pojok (bukan area baca OMR).
# Jarak >= 5 mm dari kotak pojok (cincin cek kontras r=7.2 mm) dan dari bar orientasi.
# [file, x, y_atas, tinggi] mm; lebar mengikuti rasio gambar.
# ---------------------------------------------------------------------------
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LOGOS = [
    ("logo_telkom.png", 24.0, 10.0, 8.0, "left"),
    ("logo_cswda.png", 186.0, 10.6, 6.8, "right"),
]


def draw_logos(c):
    from reportlab.lib.utils import ImageReader

    for fname, x, y, h, anchor in LOGOS:
        img = ImageReader(os.path.join(ASSETS_DIR, fname))
        iw, ih = img.getSize()
        w = h * iw / ih
        x0 = x if anchor == "left" else x - w
        c.drawImage(img, X(x0), Y(y + h), w * mm, h * mm, mask="auto")


# ---------------------------------------------------------------------------
# Teks yang boleh diedit (di versi Word jadi kotak teks; tidak dibaca OMR)
# box = [x, y, w, h] mm dari kiri-atas. size dalam pt.
# ---------------------------------------------------------------------------
TEXT_BLOCKS = [
    {
        "id": "judul",
        "box": [13, 23.2, 92, 13.6],
        "align": "center",
        "valign": "middle",
        "leading": 1.32,
        "lines": [
            {"text": "LEMBAR JAWABAN KOMPUTER", "size": 11, "bold": True},
            {"text": "PROFILING LITERASI NUMERASI", "size": 8, "bold": True, "italic": True},
            {"text": "MATH CENTER - CSWDA - TELKOM UNIVERSITY", "size": 6.5},
        ],
    },
    {
        "id": "petunjuk",
        "box": [110.5, 23.0, 86.5, 14.2],
        "align": "left",
        "lines": [
            {"text": "PETUNJUK", "size": 6.5, "bold": True},
            {"text": "1. Gunakan pensil 2B. ARSIR PENUH bulatan jawaban (bukan disilang).", "size": 5.5},
            {"text": "2. Tulis NIM & kode soal di kotak, lalu arsir digit yang sesuai di bawahnya.", "size": 5.5},
            {"text": "3. Arsir satu jawaban per nomor soal & kuisioner.", "size": 5.5},
            {"text": "4. Jika salah, hapus sebersih mungkin lalu arsir jawaban baru.", "size": 5.5},
            {"text": "5. Jangan melipat lembar & jangan mencoret 4 kotak hitam di pojok.", "size": 5.5},
        ],
    },
    {
        "id": "pernyataan",
        "box": [15, 79.0, 64, 9.6],
        "align": "left",
        "lines": [
            {
                "text": "Saya menyatakan dengan sesungguhnya bahwa saya mengerjakan ujian ini secara jujur, "
                "tanpa bantuan AI maupun orang lain.",
                "size": 6.2,
                "italic": True,
            }
        ],
    },
    {
        "id": "footer",
        "box": [24, 280.6, 162, 5],
        "align": "center",
        "lines": [
            {
                "text": "Kerjakan dengan jujur dan tenang — setiap jawabanmu membantu kami menyiapkan dukungan belajar yang tepat untukmu. Semangat!",
                "size": 6.5,
                "italic": True,
            }
        ],
    },
]


def draw_text_block(c, blk):
    from reportlab.lib.utils import simpleSplit

    x, y, w, h = blk["box"]
    lead_k = blk.get("leading", 1.18)
    if blk.get("valign") == "middle":
        total = 0.0
        for ln in blk["lines"]:
            f0 = "Helvetica-Bold" if ln.get("bold") else "Helvetica"
            total += len(simpleSplit(ln["text"], f0, ln["size"], w * mm)) * ln["size"] * lead_k * 0.3528
        y = y + max(0.0, (h - total) / 2)
    cur = y
    for ln in blk["lines"]:
        font = "Helvetica" + ("-BoldOblique" if ln.get("bold") and ln.get("italic") else "-Bold" if ln.get("bold") else "-Oblique" if ln.get("italic") else "")
        size = ln["size"]
        lead = size * lead_k * 0.3528  # pt -> mm
        for part in simpleSplit(ln["text"], font, size, w * mm):
            cur += lead
            c.setFont(font, size)
            c.setFillColor(black if ln.get("bold") else GRAY_TEXT)
            yy = cur - lead * 0.22
            if blk["align"] == "center":
                c.drawCentredString(X(x + w / 2), Y(yy), part)
            else:
                c.drawString(X(x), Y(yy), part)


# ---------------------------------------------------------------------------
# Gambar PDF
# ---------------------------------------------------------------------------
def Y(y_mm):
    """mm dari atas -> titik reportlab dari bawah."""
    return (PAGE_H - y_mm) * mm


def X(x_mm):
    return x_mm * mm


def text(c, x, y, s, size=8, bold=False, color=GRAY_TEXT, align="left", italic=False):
    font = "Helvetica"
    if bold and italic:
        font = "Helvetica-BoldOblique"
    elif bold:
        font = "Helvetica-Bold"
    elif italic:
        font = "Helvetica-Oblique"
    c.setFont(font, size)
    c.setFillColor(color)
    # y = baseline (mm dari atas)
    if align == "center":
        c.drawCentredString(X(x), Y(y), s)
    elif align == "right":
        c.drawRightString(X(x), Y(y), s)
    else:
        c.drawString(X(x), Y(y), s)


def frame(c, x, y, w, h, title=None):
    c.setStrokeColor(GRAY_FRAME)
    c.setLineWidth(0.6)
    c.roundRect(X(x), Y(y + h), w * mm, h * mm, 1.5 * mm, stroke=1, fill=0)
    if title:
        c.setFillColor(Color(0.92, 0.92, 0.92))
        c.roundRect(X(x), Y(y + 5.5), w * mm, 5.5 * mm, 1.5 * mm, stroke=0, fill=1)
        c.setStrokeColor(GRAY_FRAME)
        c.roundRect(X(x), Y(y + h), w * mm, h * mm, 1.5 * mm, stroke=1, fill=0)
        text(c, x + w / 2, y + 4.0, title, size=7.5, bold=True, align="center", color=black)


def bubble(c, cx, cy, r, label, filled=False, mark=None):
    c.setLineWidth(0.6)
    c.setStrokeColor(GRAY_LINE)
    if filled:
        c.setFillColor(Color(0.15, 0.15, 0.15))
        c.circle(X(cx), Y(cy), r * mm, stroke=1, fill=1)
        return
    c.setFillColor(white)
    c.circle(X(cx), Y(cy), r * mm, stroke=1, fill=1)
    if label is not None:
        size = 5.6 if len(label) == 1 else 4.6
        text(c, cx, cy + size * 0.352 / 2 - 0.15, label, size=size, color=GRAY_LABEL, align="center")
    if mark == "x":
        c.setStrokeColor(Color(0.15, 0.15, 0.15))
        c.setLineWidth(0.9)
        d = r * 0.75
        c.line(X(cx - d), Y(cy - d), X(cx + d), Y(cy + d))
        c.line(X(cx - d), Y(cy + d), X(cx + d), Y(cy - d))
    elif mark == "check":
        c.setStrokeColor(Color(0.15, 0.15, 0.15))
        c.setLineWidth(0.9)
        c.line(X(cx - r * 0.7), Y(cy), X(cx - r * 0.15), Y(cy + r * 0.6))
        c.line(X(cx - r * 0.15), Y(cy + r * 0.6), X(cx + r * 0.8), Y(cy - r * 0.8))
    elif mark == "half":
        c.setFillColor(Color(0.15, 0.15, 0.15))
        p = c.beginPath()
        p.moveTo(X(cx - r), Y(cy))
        p.arcTo(X(cx - r), Y(cy - r), X(cx + r), Y(cy + r), startAng=180, extent=180)
        p.close()
        c.drawPath(p, stroke=0, fill=1)


def draw_pdf(fields, path, editable_text=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = canvas.Canvas(path, pagesize=(PAGE_W * mm, PAGE_H * mm))
    c.setTitle("LJK v2 - Profiling Literasi Numerasi")
    c.setAuthor("Math Center CSWDA - Telkom University")

    # Kotak hitam pojok
    c.setFillColor(black)
    for cx, cy in MARKERS.values():
        s = MARKER_SIZE
        c.rect(X(cx - s / 2), Y(cy + s / 2), s * mm, s * mm, stroke=0, fill=1)
    # Bar orientasi
    o = ORIENTATION
    c.rect(X(o["x"]), Y(o["y"] + o["h"]), o["w"] * mm, o["h"] * mm, stroke=0, fill=1)
    draw_logos(c)

    # Header (bingkai di latar, teks bisa diedit)
    frame(c, 12, 22, 94, 16)
    frame(c, 109, 22, 89, 16)

    # Identitas (tulis tangan, tidak dibaca OMR)
    frame(c, 12, 41, 70, 72, title="IDENTITAS (tulis dengan jelas)")
    c.setStrokeColor(GRAY_FRAME)
    c.setLineWidth(0.4)
    rows = [("Nama Lengkap", 53), ("", 60.5), ("Kelas", 68.5), ("Tanggal Ujian", 76.5)]
    for label, y in rows:
        if label:
            text(c, 15, y - 3.2, label, size=6.2, color=GRAY_TEXT)
        c.line(X(15), Y(y), X(79), Y(y))
    text(c, 15, 91.6, "Tanda Tangan", size=6.2)
    c.roundRect(X(15), Y(110.5), 64 * mm, 17.5 * mm, 1 * mm, stroke=1, fill=0)

    # NIM
    nim = fields["nim"]
    frame(c, 85, 41, 71, 72, title="NIM (tulis & arsir)")
    c.setStrokeColor(GRAY_FRAME)
    c.setLineWidth(0.5)
    for i, item in enumerate(nim["items"]):
        cx = item["cells"][0][0]
        c.rect(X(cx - 2.5), Y(54.0), 5.0 * mm, 6.0 * mm, stroke=1, fill=0)
        for d, (bx, by) in enumerate(item["cells"]):
            bubble(c, bx, by, nim["r"], str(d))

    # Kode soal (3 digit, seperti NIM) + contoh pengisian
    kode = fields["kode_soal"]
    frame(c, 159, 41, 39, 72, title="KODE SOAL")
    c.setStrokeColor(GRAY_FRAME)
    c.setLineWidth(0.5)
    for item in kode["items"]:
        cx = item["cells"][0][0]
        c.rect(X(cx - 2.5), Y(54.0), 5.0 * mm, 6.0 * mm, stroke=1, fill=0)
        for d, (bx, by) in enumerate(item["cells"]):
            bubble(c, bx, by, kode["r"], str(d))
    # pemisah & kolom contoh
    c.setStrokeColor(GRAY_FRAME)
    c.setLineWidth(0.4)
    c.line(X(184.6), Y(48.5), X(184.6), Y(110))
    cx = CONTOH_X
    text(c, cx, 51.2, "CONTOH", size=5.2, bold=True, align="center", color=black)
    text(c, cx, 56.6, "BENAR", size=5.0, bold=True, align="center", color=Color(0, 0.45, 0.15))
    bubble(c, cx, 60.4, 2.0, None, filled=True)
    text(c, cx, 67.8, "SALAH", size=5.0, bold=True, align="center", color=Color(0.75, 0, 0))
    for k, mk in enumerate(["x", "check", "half"]):
        bubble(c, cx, 71.8 + k * 5.4, 2.0, None, mark=mk)
    for k, line in enumerate(["silang,", "centang,", "setengah:", "tidak", "terbaca", "mesin"]):
        text(c, cx, 88.4 + k * 2.3, line, size=4.6, align="center", italic=True)

    # Kuisioner
    kuis = fields["kuisioner"]
    frame(c, 12, 117, 186, 37, title="KUISIONER (arsir satu jawaban per nomor)")
    for item in kuis["items"]:
        lx, ly = item["label_xy"]
        text(c, lx, ly + 1.0, str(item["no"]), size=7, align="right", color=black)
        for (bx, by), lab in zip(item["cells"], kuis["options"]):
            bubble(c, bx, by, kuis["r"], lab)

    # Soal
    soal = fields["soal"]
    frame(c, 12, 158, 186, 111, title="JAWABAN SOAL (arsir penuh satu pilihan yang benar)")
    for item in soal["items"]:
        lx, ly = item["label_xy"]
        text(c, lx, ly + 1.1, str(item["no"]), size=7.5, align="right", color=black)
        for (bx, by), lab in zip(item["cells"], soal["options"]):
            bubble(c, bx, by, soal["r"], lab)

    if editable_text:
        for blk in TEXT_BLOCKS:
            draw_text_block(c, blk)

    c.showPage()
    c.save()


def main():
    fields = build_geometry()
    draw_pdf(fields, PDF_OUT)
    os.makedirs(BUILD_DIR, exist_ok=True)
    draw_pdf(fields, BG_OUT, editable_text=False)
    try:
        import pypdfium2 as pdfium

        pdfium.PdfDocument(BG_OUT)[0].render(scale=600 / 72).to_pil().convert("RGB").save(
            os.path.join(BUILD_DIR, "ljk_background_600dpi.png"), optimize=True
        )
    except ImportError:
        print("(pypdfium2 tidak ada: PNG latar untuk versi Word tidak dibuat)")
    with open(BLOCKS_OUT, "w", encoding="utf-8") as f:
        json.dump({"page": {"w": PAGE_W, "h": PAGE_H}, "blocks": TEXT_BLOCKS}, f, indent=1)
    template = {
        "version": TEMPLATE_VERSION,
        "units": "mm",
        "page": {"w": PAGE_W, "h": PAGE_H},
        "marker": {"size": MARKER_SIZE, "centers": MARKERS},
        "orientation": ORIENTATION,
        # celah putih antar-blok: header|identitas, identitas|kuisioner, kuisioner|soal, soal|footer
        "whiteRows": [39.5, 115.0, 156.0, 272.5],
        "fields": fields,
    }
    os.makedirs(os.path.dirname(JSON_OUT), exist_ok=True)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=1)
    print("PDF  ->", PDF_OUT)
    print("BG   ->", BG_OUT)
    print("JSON ->", JSON_OUT)


if __name__ == "__main__":
    main()
