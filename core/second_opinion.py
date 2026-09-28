"""Pembaca cadangan untuk NIM / nama / kode soal yang tahan fotokopi.

Pembaca utama (core.decoder) menilai "seberapa gelap" sebuah kotak. Pada fotokopi hitam-putih
kotak kosong pun sudah 35-80 % gelap (kolom berbayang abu-abu, huruf/angka tercetak lebih
tebal), sehingga silang pensil nyaris tak menambah kontras.

Di sini tiap kotak dibandingkan dengan kotak-kotak lain yang berisi huruf/angka SAMA pada blok
yang sama (median piksel demi piksel = wujud "kosong" yang khas lembar ini, lengkap dengan
bayangan dan ketebalan cetaknya). Yang dihitung hanya tinta TAMBAHAN di atas wujud kosong itu.
Setiap kotak dicari geseran terbaiknya (+-3 px) supaya selisih garis kotak tidak terhitung.

Hanya dipakai untuk melengkapi posisi yang belum terbaca oleh pembaca utama — digit/huruf yang
sudah terbaca tidak pernah ditimpa.
"""
from functools import lru_cache

import cv2
import numpy as np

_PAD = 4
_MAXSHIFT = 3
_D = 35            # selisih gelap minimum (skala 0-255) yang dianggap tinta tambahan
_INNER = 0.80

# ambang keputusan per kolom: (skor tertinggi minimum, selisih dgn urutan ke-2 minimum, z-skor minimum).
# z-skor = seberapa jauh skor tertinggi dari "derau" blok itu sendiri (median + MAD semua kotak) sehingga
# blok yang berderau tinggi otomatis lebih sulit lolos.
NUMBER_THRESH = (0.06, 0.03, 5.0)       # NIM/kode: kolom pasti berisi satu digit
TEXT_FILL_THRESH = (0.14, 0.07, 6.0)    # nama: kolom kosong itu sah -> ketat
TEXT_AMBIG_THRESH = (0.10, 0.05, 5.0)   # nama: hanya utk memutuskan '?' (terisi ganda)


def _field_size(field):
    ws = [b.get("w") or b.get("radius", 12) * 2 for it in field["items"] for b in it["bubbles"]]
    hs = [b.get("h") or b.get("radius", 12) * 2 for it in field["items"] for b in it["bubbles"]]
    return int(round(np.median(ws))), int(round(np.median(hs)))


def _patch(gray, b, bw, bh):
    hw, hh = bw // 2 + _PAD, bh // 2 + _PAD
    cx, cy = int(round(b["cx"])), int(round(b["cy"]))
    x1, y1 = cx - hw, cy - hh
    x2, y2 = x1 + bw + 2 * _PAD, y1 + bh + 2 * _PAD
    if x1 < 0 or y1 < 0 or x2 > gray.shape[1] or y2 > gray.shape[0]:
        return None
    return gray[y1:y2, x1:x2].astype(np.float32)


@lru_cache(maxsize=None)
def _inner_mask(bw, bh):
    my, mx = int(bh * (1 - _INNER) / 2), int(bw * (1 - _INNER) / 2)
    m = np.zeros((bh, bw), bool)
    m[my:bh - my, mx:bw - mx] = True
    return m


def _extra_ink(patch, tmpl, bw, bh):
    """Tinta tambahan (pecahan area dalam kotak) pada geseran terbaik."""
    inner = _inner_mask(bw, bh)
    best = 1.0
    for dy in range(-_MAXSHIFT, _MAXSHIFT + 1):
        for dx in range(-_MAXSHIFT, _MAXSHIFT + 1):
            y0, x0 = _PAD + dy, _PAD + dx
            q = patch[y0:y0 + bh, x0:x0 + bw]
            if q.shape != (bh, bw):
                continue
            s = float(((tmpl - q) > _D)[inner].mean())
            if s < best:
                best = s
    return best


def cell_scores(gray, field, passes=2):
    """{(kolom, baris): skor tinta tambahan}. Kotak di luar citra bernilai 0."""
    items = field["items"]
    bw, bh = _field_size(field)
    parity = len(items) >= 6      # bayangan selang-seling hanya relevan bila kolomnya banyak
    cells = {}
    for i, it in enumerate(items):
        for j, b in enumerate(it["bubbles"]):
            p = _patch(gray, b, bw, bh)
            if p is not None:
                cells[(i, j)] = (p, b)
    groups = {}
    for (i, j), (_, b) in cells.items():
        groups.setdefault((str(b.get("option", j)), (i % 2) if parity else 0), []).append((i, j))

    kernel = np.ones((3, 3), np.uint8)
    scores = {k: 0.0 for k in cells}
    excluded = set()
    for _ in range(passes):
        tmpls = {}
        for key, members in groups.items():
            use = [m for m in members if m not in excluded] or members
            crops = [cells[m][0][_PAD:_PAD + bh, _PAD:_PAD + bw] for m in use]
            tmpls[key] = cv2.erode(np.median(np.stack(crops), axis=0), kernel)
        for (i, j), (p, b) in cells.items():
            key = (str(b.get("option", j)), (i % 2) if parity else 0)
            scores[(i, j)] = _extra_ink(p, tmpls[key], bw, bh)
        # putaran ke-2: kotak yang jelas bertinta dikeluarkan dari perhitungan "wujud kosong"
        cut = max(0.05, float(np.percentile(list(scores.values()), 90)))
        excluded = {k for k, v in scores.items() if v >= cut}
    return scores, items


def noise_level(scores):
    """(median, sigma-robust) semua kotak blok — sebagian besar kotak kosong, jadi ini ~ derau latar."""
    v = np.array(list(scores.values()) or [0.0])
    med = float(np.median(v))
    return med, 1.4826 * float(np.median(np.abs(v - med)))


def column_reading(scores, items, col, noise=None):
    """(label, skor_tertinggi, selisih_dg_ke2, z) untuk satu kolom."""
    bubbles = items[col]["bubbles"]
    v = np.array([scores.get((col, j), 0.0) for j in range(len(bubbles))])
    order = np.argsort(-v)
    top = float(v[order[0]])
    second = float(v[order[1]]) if len(v) > 1 else 0.0
    med, sigma = noise if noise is not None else noise_level(scores)
    z = (top - med) / max(sigma, 0.01)
    return str(bubbles[order[0]].get("option", order[0])), top, top - second, z


def _unresolved(text, n):
    chars = list(text.ljust(n))[:n]
    return chars, [i for i, c in enumerate(chars) if c in (" ", "?")]


def refine_identity(gray, fields, decoded, reg_info=None):
    """Lengkapi NIM/kode/nama pada `decoded` (dict hasil decode_field) — hanya posisi yang belum terbaca."""
    out = dict(decoded)
    reg_info = reg_info or {}
    for name, kind in (("NPM", "number"), ("KODE SOAL", "number"), ("NAMA", "text")):
        field = fields.get(name)
        if not field or name not in out:
            continue
        if not reg_info.get(name, {}).get("aligned"):
            continue        # posisi kisi tak terverifikasi -> skor "tinta tambahan" tak bermakna, jangan menebak
        n = len(field["items"])
        chars, todo = _unresolved(out[name], n)
        if kind == "text":
            shifted = any(abs(reg_info.get("NAMA", {}).get(k, 0)) >= 5 for k in ("dx", "dy"))
            if "?" not in out[name] and not shifted:
                continue
        if not todo:
            continue
        scores, items = cell_scores(gray, field)
        noise = noise_level(scores)
        for i in todo:
            label, top, gap, z = column_reading(scores, items, i, noise)
            lim = NUMBER_THRESH if kind == "number" else (TEXT_AMBIG_THRESH if chars[i] == "?" else TEXT_FILL_THRESH)
            if top >= lim[0] and gap >= lim[1] and z >= lim[2]:
                chars[i] = label
        out[name] = "".join(chars).rstrip()
    return out
