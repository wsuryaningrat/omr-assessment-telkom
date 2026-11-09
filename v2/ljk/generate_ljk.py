"""Generator LJK v2 (Pendidikan Karakter) + template koordinat OMR.

PDF dan JSON koordinat dibuat dari SATU sumber geometri di file ini, jadi posisi
bubble di template selalu persis sama dengan yang tercetak (tidak perlu kalibrasi
manual seperti v1).

Semua koordinat dalam milimeter, origin di pojok KIRI-ATAS halaman A4 (210 x 297).

Jalankan:
    python v2/ljk/generate_ljk.py
Output:
    v2/public/ljk/LJK_v2.pdf
    v2/lib/omr/template.json
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

TEMPLATE_VERSION = "ljk-v2-pendikar-1"
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

KODE_SOAL = ["192", "273", "365", "487"]
N_SOAL = 75
N_KUIS = 15
NIM_LEN = 12

GRAY_LINE = Color(0.35, 0.35, 0.35)
GRAY_LABEL = Color(0.62, 0.62, 0.62)
GRAY_FRAME = Color(0.55, 0.55, 0.55)
GRAY_TEXT = Color(0.2, 0.2, 0.2)


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

    # Kode soal: 4 pilihan vertikal
    kode_r = 2.4
    kode_cells = [[167.0, 54.0 + i * 8.0] for i in range(len(KODE_SOAL))]
    fields["kode_soal"] = {
        "kind": "single",
        "label": "Kode Soal",
        "options": KODE_SOAL,
        "r": kode_r,
        "items": [{"id": "kode", "cells": kode_cells}],
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


def draw_pdf(fields, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = canvas.Canvas(path, pagesize=(PAGE_W * mm, PAGE_H * mm))
    c.setTitle("LJK v2 - Profiling Literasi Numerik - Pendidikan Karakter")
    c.setAuthor("Math Center CSWDA - Telkom University")

    # Kotak hitam pojok
    c.setFillColor(black)
    for cx, cy in MARKERS.values():
        s = MARKER_SIZE
        c.rect(X(cx - s / 2), Y(cy + s / 2), s * mm, s * mm, stroke=0, fill=1)
    # Bar orientasi
    o = ORIENTATION
    c.rect(X(o["x"]), Y(o["y"] + o["h"]), o["w"] * mm, o["h"] * mm, stroke=0, fill=1)
    text(c, 52, 15.4, "Bagian ATAS lembar", size=6.5, color=GRAY_LABEL, align="center", italic=True)
    text(c, 158, 15.4, TEMPLATE_VERSION, size=6.5, color=GRAY_LABEL, align="center")

    # Header
    frame(c, 12, 22, 94, 16)
    text(c, 59, 27.2, "LEMBAR JAWABAN KOMPUTER", size=11, bold=True, align="center", color=black)
    text(c, 59, 31.4, "PROFILING LITERASI NUMERIK", size=8, bold=True, italic=True, align="center")
    text(c, 59, 35.0, "MATH CENTER - CSWDA - TELKOM UNIVERSITY  |  MK PENDIDIKAN KARAKTER", size=6, align="center")

    frame(c, 109, 22, 89, 16)
    petunjuk = [
        "1. Gunakan pensil 2B. ARSIR PENUH bulatan jawaban (bukan disilang).",
        "2. Tulis NIM di kotak, lalu arsir digit yang sesuai di bawahnya.",
        "3. Arsir SATU kode soal sesuai kode pada naskah soal.",
        "4. Jika salah, hapus sebersih mungkin lalu arsir jawaban baru.",
        "5. Jangan melipat lembar & jangan mencoret 4 kotak hitam di pojok.",
    ]
    text(c, 111, 25.6, "PETUNJUK", size=6.5, bold=True, color=black)
    for i, line in enumerate(petunjuk):
        text(c, 111, 28.4 + i * 2.2, line, size=5.5)

    # Identitas (tulis tangan, tidak dibaca OMR)
    frame(c, 12, 41, 70, 72, title="IDENTITAS (tulis dengan jelas)")
    c.setStrokeColor(GRAY_FRAME)
    c.setLineWidth(0.4)
    rows = [("Nama Lengkap", 53), ("", 61), ("Kelas", 70), ("Program Studi", 79), ("Tanggal Ujian", 88)]
    for label, y in rows:
        if label:
            text(c, 15, y - 3.2, label, size=6.2, color=GRAY_TEXT)
        c.line(X(15), Y(y), X(79), Y(y))
    text(c, 15, 92.8, "Tanda Tangan", size=6.2)
    c.roundRect(X(15), Y(110), 64 * mm, 16 * mm, 1 * mm, stroke=1, fill=0)

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

    # Kode soal + contoh pengisian
    kode = fields["kode_soal"]
    frame(c, 159, 41, 39, 72, title="KODE SOAL")
    text(c, 178.5, 49.6, "arsir satu", size=5.6, align="center", italic=True)
    for (bx, by), lab in zip(kode["items"][0]["cells"], kode["options"]):
        bubble(c, bx, by, kode["r"], None)
        text(c, bx + 4.5, by + 1.2, lab, size=8.5, bold=True, color=black)

    c.setStrokeColor(GRAY_FRAME)
    c.setLineWidth(0.4)
    c.line(X(161), Y(82), X(196), Y(82))
    text(c, 178.5, 86.0, "CONTOH PENGISIAN", size=5.8, bold=True, align="center", color=black)
    text(c, 162, 91.6, "BENAR", size=5.8, bold=True, color=Color(0, 0.45, 0.15))
    for k, lab in enumerate("ABCD"):
        bubble(c, 175 + k * 5.4, 90.6, 2.0, lab, filled=(lab == "B"))
    text(c, 162, 99.0, "SALAH", size=5.8, bold=True, color=Color(0.75, 0, 0))
    for k, (lab, mk) in enumerate(zip("ABCD", ["x", "check", None, "half"])):
        bubble(c, 175 + k * 5.4, 98.0, 2.0, lab, mark=mk)
    text(c, 178.5, 105.0, "silang / centang / setengah", size=5.0, align="center", italic=True)
    text(c, 178.5, 108.2, "TIDAK terbaca mesin", size=5.0, align="center", italic=True)

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

    # Footer di antara kotak hitam bawah
    text(c, 105, 282.0, "Saat difoto, pastikan KEEMPAT kotak hitam di pojok terlihat jelas dan tidak tertutup jari/bayangan.",
         size=6.0, align="center", italic=True)
    text(c, 105, 285.0, "Saya mengerjakan ujian ini secara jujur tanpa bantuan AI & orang lain.", size=6.0, align="center")

    c.showPage()
    c.save()


def main():
    fields = build_geometry()
    draw_pdf(fields, PDF_OUT)
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
    print("JSON ->", JSON_OUT)


if __name__ == "__main__":
    main()
