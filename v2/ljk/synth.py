"""Generator foto LJK v2 sintetis untuk menguji engine OMR (lib/omr).

Merender LJK_v2.pdf, mengarsir jawaban acak ala pensil, lalu mensimulasikan
foto HP: perspektif, rotasi, blur, noise, bayangan, resolusi rendah (WhatsApp),
benda gelap di sekitar kertas, fotokopi pucat, dan lembar terbalik 180 derajat.

    python v2/ljk/synth.py            # -> v2/lib/omr/__fixtures__/*.jpg + truth.json
"""
import json
import os
import random

import cv2
import numpy as np
import pypdfium2 as pdfium

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PDF = os.path.join(ROOT, "public", "ljk", "LJK_v2.pdf")
TPL = os.path.join(ROOT, "lib", "omr", "template.json")
OUT = os.path.join(ROOT, "lib", "omr", "__fixtures__")

DPI = 200
PX_PER_MM = DPI / 25.4


def render_blank():
    pdf = pdfium.PdfDocument(PDF)
    img = pdf[0].render(scale=DPI / 72).to_numpy()
    img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR) if img.shape[2] == 4 else cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img


def pencil_fill(img, cx, cy, r, rng, strength=1.0, partial=1.0):
    """Arsiran pensil: elips sedikit meleset + tekstur goresan."""
    mask = np.zeros(img.shape[:2], np.uint8)
    rx = r * rng.uniform(0.85, 1.1) * partial
    ry = r * rng.uniform(0.8, 1.05)
    ox, oy = rng.uniform(-0.15, 0.15) * r, rng.uniform(-0.15, 0.15) * r
    cv2.ellipse(mask, (int(cx + ox), int(cy + oy)), (int(rx), int(ry)), rng.uniform(0, 180), 0, 360, 255, -1)
    level = rng.uniform(40, 95) / strength  # graphite gelap tapi bukan hitam pekat
    noise = rng.normal(0, 18, img.shape[:2])
    tone = np.clip(level + noise, 0, 255)
    m = mask > 0
    for ch in range(3):
        img[..., ch][m] = np.minimum(img[..., ch][m], tone[m]).astype(np.uint8)


def pencil_x(img, cx, cy, r, rng):
    d = int(r * 0.8)
    th = max(2, int(r * 0.18))
    col = int(rng.uniform(50, 100))
    cv2.line(img, (int(cx - d), int(cy - d)), (int(cx + d), int(cy + d)), (col,) * 3, th, cv2.LINE_AA)
    cv2.line(img, (int(cx - d), int(cy + d)), (int(cx + d), int(cy - d)), (col,) * 3, th, cv2.LINE_AA)


def erased_smudge(img, cx, cy, r, rng):
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.circle(mask, (int(cx), int(cy)), int(r * 0.9), 255, -1)
    m = mask > 0
    for ch in range(3):
        img[..., ch][m] = np.minimum(img[..., ch][m], int(rng.uniform(195, 220))).astype(np.uint8)


def fill_sheet(img, tpl, rng, style="fill"):
    truth = {}
    f = tpl["fields"]

    def mm(p):
        return p[0] * PX_PER_MM, p[1] * PX_PER_MM

    # NIM 12 digit realistis: 1030 + 8 digit
    nim = "1030" + "".join(str(rng.integers(0, 10)) for _ in range(8))
    truth["nim"] = nim
    for c, item in enumerate(f["nim"]["items"]):
        cx, cy = mm(item["cells"][int(nim[c])])
        pencil_fill(img, cx, cy, f["nim"]["r"] * PX_PER_MM, rng)

    k = int(rng.integers(0, 4))
    truth["kode_soal"] = f["kode_soal"]["options"][k]
    cx, cy = mm(f["kode_soal"]["items"][0]["cells"][k])
    pencil_fill(img, cx, cy, f["kode_soal"]["r"] * PX_PER_MM, rng)

    for name in ("kuisioner", "soal"):
        fd = f[name]
        ans = []
        for item in fd["items"]:
            u = rng.random()
            if u < 0.06:
                ans.append("")  # kosong
                if rng.random() < 0.3:
                    o = int(rng.integers(0, 4))
                    erased_smudge(img, *mm(item["cells"][o]), fd["r"] * PX_PER_MM, rng)
                continue
            o = int(rng.integers(0, 4))
            r = fd["r"] * PX_PER_MM
            if style == "mixed":
                kind = rng.random()
                if kind < 0.12:  # jawaban ganda
                    o2 = (o + 1 + int(rng.integers(0, 3))) % 4
                    pencil_fill(img, *mm(item["cells"][o]), r, rng)
                    pencil_fill(img, *mm(item["cells"][o2]), r, rng)
                    ans.append("MULTI")
                    continue
                if kind < 0.22:  # silang di tengah lembar arsiran
                    pencil_x(img, *mm(item["cells"][o]), r, rng)
                    ans.append("ABCD"[o] + "?")
                    continue
                if kind < 0.30:  # arsiran tipis / setengah
                    pencil_fill(img, *mm(item["cells"][o]), r, rng, strength=0.45, partial=0.6)
                    ans.append("ABCD"[o] + "?")
                    continue
            if style == "x":
                pencil_x(img, *mm(item["cells"][o]), r, rng)
                ans.append("ABCD"[o] + "?")  # silang: boleh terbaca / ditandai ragu
                continue
            pencil_fill(img, *mm(item["cells"][o]), r, rng, strength=rng.uniform(0.8, 1.2))
            ans.append("ABCD"[o])
            # sesekali jawaban lama dihapus (bekas hapusan)
            if rng.random() < 0.05:
                o2 = (o + 1 + int(rng.integers(0, 3))) % 4
                erased_smudge(img, *mm(item["cells"][o2]), r, rng)
        truth[name] = ans
    return truth


def photograph(sheet, rng, *, out_size=(3024, 4032), tilt=0.06, rot_deg=6, blur=0.0, noise=4,
               shadow=0.25, clutter=True, near_corner=False, rotate180=False, photocopy=False, jpeg=88, fill_frac=0.80):
    h, w = sheet.shape[:2]
    if photocopy:
        g = cv2.cvtColor(sheet, cv2.COLOR_BGR2GRAY).astype(np.float32)
        g = 255 - (255 - g) * 0.7 + 18  # kontras turun, hitam jadi abu
        g = cv2.GaussianBlur(g, (0, 0), 1.2)
        sheet = cv2.cvtColor(np.clip(g, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    if rotate180:
        sheet = cv2.rotate(sheet, cv2.ROTATE_180)

    W, H = out_size
    bg = np.full((H, W, 3), rng.integers(70, 150), np.uint8)
    bg = cv2.add(bg, rng.normal(0, 12, bg.shape).astype(np.int16).clip(-40, 40).astype(np.uint8))
    bg = cv2.GaussianBlur(bg, (0, 0), 3)

    # Posisi kertas di frame
    sh = H * fill_frac
    sw = sh * w / h
    if sw > W * 0.95:
        sw = W * 0.95
        sh = sw * h / w
    cx, cy = W / 2 + rng.uniform(-0.04, 0.04) * W, H / 2 + rng.uniform(-0.03, 0.03) * H
    base = np.array([[-sw / 2, -sh / 2], [sw / 2, -sh / 2], [sw / 2, sh / 2], [-sw / 2, sh / 2]])
    a = np.deg2rad(rng.uniform(-rot_deg, rot_deg))
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    dst = base @ R.T
    dst += rng.uniform(-tilt, tilt, dst.shape) * np.array([sw, sh])
    dst += [cx, cy]
    # pastikan seluruh kertas (termasuk 4 kotak hitam) ada di dalam frame
    for _ in range(20):
        if dst[:, 0].min() > 0.02 * W and dst[:, 0].max() < 0.98 * W and dst[:, 1].min() > 0.02 * H and dst[:, 1].max() < 0.98 * H:
            break
        c0 = dst.mean(axis=0)
        dst = c0 + (dst - c0) * 0.93
        dst += (np.array([W / 2, H / 2]) - dst.mean(axis=0)) * 0.3
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(src, np.float32(dst))
    warped = cv2.warpPerspective(sheet, M, (W, H), flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), M, (W, H))
    img = bg.copy()
    img[mask > 0] = warped[mask > 0]

    if clutter:  # benda gelap (pulpen, HP, tas) di luar kertas
        for _ in range(int(rng.integers(2, 5))):
            x, y = int(rng.uniform(0, W)), int(rng.uniform(0, H))
            s = int(rng.uniform(0.02, 0.09) * W)
            y2 = y + int(s * rng.uniform(0.6, 1.4))
            if mask[max(0, y - 20):min(H, y2 + 20), max(0, x - 20):min(W, x + s + 20)].any():
                continue
            cv2.rectangle(img, (x, y), (x + s, y2), (20, 20, 25), -1)

    if near_corner:  # kotak hitam seukuran marker tepat di luar pojok kiri-bawah & kanan-atas
        px_mm = sh / 297.0
        for (mx, my), (dx, dy) in (((14, 283), (-2, 30)), ((196, 14), (12, -26))):
            p = cv2.perspectiveTransform(np.float32([[[mx * w / 210, my * h / 297]]]), M)[0, 0]
            s = int(10 * px_mm)
            x0, y0 = int(p[0] + dx * px_mm), int(p[1] + dy * px_mm)
            cv2.rectangle(img, (x0 - s // 2, y0 - s // 2), (x0 + s // 2, y0 + s // 2), (20, 20, 22), -1)

    # Bayangan / gradasi cahaya
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ang = rng.uniform(0, 2 * np.pi)
    grad = (np.cos(ang) * xx / W + np.sin(ang) * yy / H)
    grad = (grad - grad.min()) / (grad.max() - grad.min() + 1e-6)
    light = 1.0 - shadow * grad
    img = np.clip(img.astype(np.float32) * light[..., None], 0, 255)
    # warna lampu kekuningan
    img[..., 0] *= rng.uniform(0.88, 1.0)
    img = img.astype(np.uint8)

    if blur > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    if noise > 0:
        img = np.clip(img.astype(np.int16) + rng.normal(0, noise, img.shape).astype(np.int16), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


CASES = [
    # name, photograph kwargs, fill style
    ("01_bersih", dict(tilt=0.02, rot_deg=2, shadow=0.1), "fill"),
    ("02_miring_perspektif", dict(tilt=0.09, rot_deg=10, shadow=0.2), "fill"),
    ("03_whatsapp_1200x1600", dict(out_size=(1200, 1600), jpeg=70, tilt=0.04), "fill"),
    ("04_blur", dict(blur=2.2, tilt=0.05), "fill"),
    ("05_bayangan_kuat", dict(shadow=0.55, tilt=0.05), "fill"),
    ("06_terbalik_180", dict(rotate180=True, tilt=0.04), "fill"),
    ("07_fotokopi_pucat", dict(photocopy=True, tilt=0.04, shadow=0.2), "fill"),
    ("08_jauh_kecil", dict(fill_frac=0.5, tilt=0.03), "fill"),
    ("09_landscape_hp", dict(out_size=(4032, 3024), rot_deg=90, tilt=0.03, fill_frac=0.95), "fill"),
    ("10_noise_tinggi", dict(noise=14, jpeg=65, tilt=0.05), "fill"),
    ("11_silang_x", dict(tilt=0.04), "x"),
    ("12_hp_1080p", dict(out_size=(1080, 1920), jpeg=80, tilt=0.04, fill_frac=0.85), "fill"),
    ("13_campur_ganda_tipis", dict(tilt=0.05), "mixed"),
    ("14_benda_hitam_dekat_pojok", dict(tilt=0.03, fill_frac=0.72, near_corner=True), "fill"),
    ("15_benda_dekat_pojok_wa", dict(out_size=(1080, 1920), jpeg=75, tilt=0.03, fill_frac=0.75, near_corner=True), "fill"),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    tpl = json.load(open(TPL))
    blank = render_blank()
    truth_all = {}
    for i, (name, kw, style) in enumerate(CASES):
        rng = np.random.default_rng(1000 + i)
        sheet = blank.copy()
        truth = fill_sheet(sheet, tpl, rng, style=style)
        photo = photograph(sheet, rng, **kw)
        # Simpan ukuran wajar agar repo tidak bengkak
        hh, ww = photo.shape[:2]
        s = min(1.0, 2400 / max(hh, ww))
        if s < 1:
            photo = cv2.resize(photo, (int(ww * s), int(hh * s)), interpolation=cv2.INTER_AREA)
        path = os.path.join(OUT, f"{name}.jpg")
        cv2.imwrite(path, photo, [cv2.IMWRITE_JPEG_QUALITY, 85])
        truth["style"] = style
        truth_all[name] = truth
        print(name, photo.shape)
    # Kasus negatif: foto tanpa LJK
    rng = np.random.default_rng(7)
    neg = np.full((1600, 1200, 3), 120, np.uint8)
    for _ in range(6):
        x, y, s = int(rng.uniform(0, 1100)), int(rng.uniform(0, 1500)), int(rng.uniform(40, 160))
        cv2.rectangle(neg, (x, y), (x + s, y + s), (15, 15, 15), -1)
    cv2.imwrite(os.path.join(OUT, "99_bukan_ljk.jpg"), neg)
    truth_all["99_bukan_ljk"] = {"expect_fail": True}
    json.dump(truth_all, open(os.path.join(OUT, "truth.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
