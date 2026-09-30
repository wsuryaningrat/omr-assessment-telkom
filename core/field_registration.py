"""Koreksi posisi blok kotak (field) LJK terhadap garis kotak yang benar-benar tercetak.

Latar belakang: crop LJK ditentukan oleh bingkai/marker sudut, tetapi isi lembar tidak selalu
tepat di tempat yang dijanjikan template. Fotokopi (mesin salin menskalakan/menggeser isi
1-3 %) menggeser blok NIM/nama/kode sampai 10-16 piksel — setengah tinggi satu kotak — sehingga
pembaca mengukur di antara kotak dan semua bubble tampak kosong/ganda.

Cara kerja: per blok, garis kotak template dirender sebagai "kisi" lalu dicocokkan ke citra
(korelasi silang kasar, dihaluskan dengan ECC affine). Hasilnya dipakai menggeser koordinat
bubble blok itu saja. Koreksi HANYA dipakai bila jelas lebih baik dari posisi semula dan
parameternya masuk akal; selain itu blok dibiarkan persis seperti template (lembar cetakan asli
tidak berubah perilakunya).
"""
import copy

import cv2
import numpy as np

_MARGIN = 32        # jangkauan pencarian geser (px kanvas)
_SCALE = 0.5        # kerja di setengah resolusi supaya murah (presisi tetap sub-piksel di kanvas)
_MIN_PEAK = 0.20    # korelasi minimum agar kisi dianggap ditemukan
_MIN_GAIN = 0.02    # korelasi harus naik segini dibanding posisi semula, kalau tidak: biarkan
_MAX_SCALE_DEV = 0.06
_MAX_SHEAR = 0.05
_MAX_SHIFT_FRAC = 0.68   # geser maksimum sebagai pecahan jarak antar-kotak (kisi periodik: lebih jauh = ambigu)
_MIN_LINE_CONTRAST = 12.0   # kontras garis-vs-isi minimum untuk tiap kolom dan tiap baris kisi
_ALIGNED_MEAN = 30.0        # kontras rata-rata di posisi template yang menandakan blok sudah pas
_MIN_FINAL_ANSWER = 30.0     # blok jawaban (4 kolom x 15 baris, garis kisi lebih jarang): ambang lebih rendah, syarat per-baris/kolom tetap
_MIN_FINAL_CONTRAST = 52.0  # kontras rata-rata di posisi baru: fotokopi asli 54-80; "pendaratan" satu periode meleset <= ~50
_WEAK_FINAL_CONTRAST = 32.0  # kandidat 'lemah' (foto beresolusi rendah): diterima HANYA bila didukung >= 2 blok lain yang bergeser searah
_CONSENSUS_DX, _CONSENSUS_DY, _CONSENSUS_MIN = 14.0, 10.0, 2
_MIN_CONTRAST_GAIN = 15.0  # garis kotak harus lebih gelap dari isi kotak sebesar ini (skala 0-255) dibanding posisi semula


def _cells(field):
    return [b for it in field["items"] for b in it["bubbles"]]


def _lattice(cells, x0, y0, w, h):
    lat = np.full((h, w), 255, np.uint8)
    for b in cells:
        bx, by = int(round(b["x"] - x0)), int(round(b["y"] - y0))
        cv2.rectangle(lat, (bx, by), (bx + int(round(b["w"])), by + int(round(b["h"]))), 0, 2)
    return lat


def _highpass(img, s1=0.8, s2=6.0):
    f = img.astype(np.float32)
    return cv2.GaussianBlur(f, (0, 0), s1) - cv2.GaussianBlur(f, (0, 0), s2)


def _pitch(cells):
    """Jarak khas antar-kotak (px) pada sumbu x dan y."""
    def med_step(vals):
        u = sorted(set(round(v) for v in vals))
        d = [b - a for a, b in zip(u, u[1:]) if b - a > 6]
        return float(np.median(d)) if d else 1e9
    return med_step([b["cx"] for b in cells]), med_step([b["cy"] for b in cells])


def _transform_points(pts, W):
    """Petakan titik (N,2) lewat W: affine (2x3) atau perspektif (3x3, dari estimate_field_perspective).
    Satu fungsi dipakai di mana pun koordinat template->citra perlu dihitung, supaya affine (lama) dan
    perspektif (layer 2, baru) diperlakukan seragam oleh _cell_contrasts/apply_warp."""
    pts = np.asarray(pts, dtype=np.float64)
    if W.shape[0] == 3:
        out = cv2.perspectiveTransform(pts.reshape(-1, 1, 2).astype(np.float32), W.astype(np.float32))
        return out.reshape(-1, 2).astype(np.float64)
    return pts @ W[:, :2].T + W[:, 2]


def _cell_contrasts(img, cells, x0, y0, W=None):
    """Kontras tiap kotak = rata-rata abu-abu isi kotak - rata-rata abu-abu garis kotak (positif = garis
    tercetak memang jatuh di garis kisi). W (koord. template->citra; affine 2x3 atau perspektif 3x3) opsional.
    Kembalikan array (kontras, kolom, baris) per kotak."""
    h, w = img.shape[:2]
    out = []
    for b in cells:
        bx, by = b["x"] - x0, b["y"] - y0
        bw, bh = b["w"], b["h"]
        xs, ys = (bx, bx + bw), (by, by + bh)
        if W is not None:
            pts = _transform_points([(x, y) for x in xs for y in ys], W)
            xs = (pts[:, 0].min(), pts[:, 0].max())
            ys = (pts[:, 1].min(), pts[:, 1].max())
        x1, x2, y1, y2 = int(round(xs[0])), int(round(xs[1])), int(round(ys[0])), int(round(ys[1]))
        if x1 < 2 or y1 < 2 or x2 > w - 2 or y2 > h - 2 or x2 - x1 < 14 or y2 - y1 < 14:
            out.append((0.0, b.get("col", 0), b.get("row", 0)))
            continue
        inside = img[y1 + 6:y2 - 5, x1 + 6:x2 - 5]
        strips = np.concatenate([img[y1 - 1:y1 + 2, x1:x2].ravel(), img[y2 - 1:y2 + 2, x1:x2].ravel(),
                                 img[y1:y2, x1 - 1:x1 + 2].ravel(), img[y1:y2, x2 - 1:x2 + 2].ravel()])
        out.append((float(inside.mean() - strips.mean()), b.get("col", 0), b.get("row", 0)))
    return out


def _grid_ok(contrasts):
    """(rata-rata, min per-kolom, min per-baris)."""
    vals = np.array([c for c, _, _ in contrasts])
    by_col, by_row = {}, {}
    for c, col, row in contrasts:
        by_col.setdefault(col, []).append(c)
        by_row.setdefault(row, []).append(c)
    return (float(vals.mean()),
            min(float(np.mean(v)) for v in by_col.values()),
            min(float(np.mean(v)) for v in by_row.values()))


def estimate_field_warp(gray, field, margin=_MARGIN, scale=_SCALE, min_final=_MIN_FINAL_CONTRAST):
    """Kembalikan (W 2x3 di koordinat kanvas *lokal crop*, origin (x0,y0), info) atau (None, origin, info)
    bila blok sebaiknya dibiarkan. W memetakan koordinat template -> koordinat citra."""
    cells = _cells(field)
    xs = [b["x"] for b in cells]
    ys = [b["y"] for b in cells]
    xe = [b["x"] + b["w"] for b in cells]
    ye = [b["y"] + b["h"] for b in cells]
    x0, y0 = max(0, int(min(xs)) - margin), max(0, int(min(ys)) - margin)
    x1, y1 = min(gray.shape[1], int(max(xe)) + margin), min(gray.shape[0], int(max(ye)) + margin)
    info = {"applied": False, "aligned": False}
    if x1 - x0 < 40 or y1 - y0 < 40:
        return None, (x0, y0), info

    full_img = gray[y0:y1, x0:x1]
    m0, min_col0, min_row0 = _grid_ok(_cell_contrasts(full_img, cells, x0, y0))
    info.update(contrast0=round(m0, 1))
    if m0 >= _ALIGNED_MEAN and min_col0 >= _MIN_LINE_CONTRAST and min_row0 >= _MIN_LINE_CONTRAST:
        info["aligned"] = True     # garis tercetak sudah jatuh di kisi template: jangan diutak-atik
        return None, (x0, y0), info

    img = full_img
    lat = _lattice(cells, x0, y0, x1 - x0, y1 - y0)
    if scale != 1.0:
        size = (max(8, int((x1 - x0) * scale)), max(8, int((y1 - y0) * scale)))
        img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        lat = cv2.resize(lat, size, interpolation=cv2.INTER_AREA)
    Ih, Th = _highpass(img), _highpass(lat)

    m = int(round(margin * scale))
    if Th.shape[0] <= 2 * m + 4 or Th.shape[1] <= 2 * m + 4:
        return None, (x0, y0), info
    res = cv2.matchTemplate(Ih, Th[m:Th.shape[0] - m, m:Th.shape[1] - m], cv2.TM_CCOEFF_NORMED)
    _, peak, _, loc = cv2.minMaxLoc(res)
    zero = float(res[m, m]) if res.shape[0] > m and res.shape[1] > m else 0.0
    info.update(peak=round(float(peak), 3), zero=round(zero, 3))
    if peak < _MIN_PEAK or peak - zero < _MIN_GAIN:
        return None, (x0, y0), info

    W = np.array([[1, 0, loc[0] - m], [0, 1, loc[1] - m]], dtype=np.float32)
    n_cols = len({round(b["cx"] / 8) for b in cells})
    n_rows = len({round(b["cy"] / 8) for b in cells})
    motion = cv2.MOTION_AFFINE if min(n_cols, n_rows) >= 5 else cv2.MOTION_TRANSLATION
    try:
        _, W = cv2.findTransformECC(Th, Ih, W, motion,
                                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 150, 1e-5), None, 7)
    except cv2.error:
        pass   # tetap pakai translasi kasar

    A, t = W[:, :2].astype(np.float64), W[:, 2].astype(np.float64)
    if scale != 1.0:   # kembalikan ke koordinat kanvas penuh (memperhitungkan pusat piksel saat resize)
        c = np.array([(scale - 1) / 2.0] * 2)
        t = ((A - np.eye(2)) @ c + t) / scale
    px, py = _pitch(cells)
    lim_x, lim_y = min(margin, _MAX_SHIFT_FRAC * px), min(margin, _MAX_SHIFT_FRAC * py)
    if not (abs(A[0, 0] - 1) <= _MAX_SCALE_DEV and abs(A[1, 1] - 1) <= _MAX_SCALE_DEV
            and abs(A[0, 1]) <= _MAX_SHEAR and abs(A[1, 0]) <= _MAX_SHEAR
            and abs(t[0]) <= lim_x and abs(t[1]) <= lim_y):
        info["rejected"] = "parameter"
        info["kandidat"] = dict(sx=round(float(A[0, 0]), 3), sy=round(float(A[1, 1]), 3), shx=round(float(A[0, 1]), 3),
                                shy=round(float(A[1, 0]), 3), dx=round(float(t[0]), 1), dy=round(float(t[1]), 1),
                                lim=(round(lim_x, 1), round(lim_y, 1)))
        return None, (x0, y0), info
    Wfull = np.hstack([A, t[:, None]]).astype(np.float32)
    m1, min_col, min_row = _grid_ok(_cell_contrasts(full_img, cells, x0, y0, Wfull))
    info.update(contrast1=round(m1, 1), min_col=round(min_col, 1), min_row=round(min_row, 1))
    # kisi berulang bisa "mendarat" satu periode meleset; tolak kecuali SETIAP kolom & baris jatuh di garis tercetak
    line_ok = min_col >= _MIN_LINE_CONTRAST and min_row >= _MIN_LINE_CONTRAST and m1 - m0 >= _MIN_CONTRAST_GAIN
    if line_ok and m1 < min_final and m1 >= _WEAK_FINAL_CONTRAST:
        # kandidat lemah: baris/kolom semua jatuh di garis tetapi kontras rata-rata sedang -> serahkan ke konsensus antar-blok
        info.update(weak=True, dx=round(float(t[0]), 1), dy=round(float(t[1]), 1),
                    sx=round(float(A[0, 0]), 3), sy=round(float(A[1, 1]), 3))
        return Wfull, (x0, y0), info
    if m1 < min_final or not line_ok:
        info["rejected"] = "kontras"
        return None, (x0, y0), info
    info.update(applied=True, aligned=True, dx=round(float(t[0]), 1), dy=round(float(t[1]), 1),
                sx=round(float(A[0, 0]), 3), sy=round(float(A[1, 1]), 3))
    return Wfull, (x0, y0), info


def apply_warp(field, W, origin):
    """Salinan field dengan semua koordinat bubble dipetakan lewat W (koordinat template -> citra).
    W boleh affine (2x3, dari estimate_field_warp) atau perspektif (3x3, dari estimate_field_perspective)."""
    out = copy.deepcopy(field)
    x0, y0 = origin
    # Kumpulkan tiap (bubble, pasangan-koordinat) sbg satu entri supaya _transform_points dipanggil
    # sekali (perspektif lewat cv2.perspectiveTransform lebih murah dipanggil batch daripada per titik).
    entries = [(b, kx, ky) for it in out["items"] for b in it["bubbles"] for kx, ky in (("cx", "cy"), ("x", "y"))]
    pts = [(b[kx] - x0, b[ky] - y0) for b, kx, ky in entries]
    mapped = _transform_points(pts, W)
    for (b, kx, ky), (nx, ny) in zip(entries, mapped):
        b[kx], b[ky] = float(nx) + x0, float(ny) + y0
    return out


_SNAP_RX, _SNAP_RY, _SNAP_MIN_CORR, _SNAP_MAX_SPREAD = 14, 18, 0.30, 6.0
_CORNER_SEARCH_RADIUS = 48   # jendela pencarian tiap sudut blok (px kanvas) -- jauh lebih lebar dari snap_rows
_CORNER_MIN_CORR = 0.30      # korelasi minimum agar satu sudut dianggap ditemukan


def _outline(w, h, pad):
    t = np.full((h + 2 * pad, w + 2 * pad), 255, np.uint8)
    cv2.rectangle(t, (pad, pad), (pad + w, pad + h), 0, 2)
    return _highpass(t)


def _corner_cells(field):
    """4 sel bubble paling ujung suatu blok (kiri-atas/kanan-atas/kanan-bawah/kiri-bawah), dipakai sbg
    'marker' pengganti ArUco utk homografi lokal blok itu -- layer 2. None bila blok terlalu sempit
    (mis. FAKULTAS: 1 kolom saja) sehingga 4 sudut tak bisa berbeda posisi di kedua sumbu."""
    cells = _cells(field)
    if len(cells) < 4:
        return None
    tl = min(cells, key=lambda b: b["cx"] + b["cy"])
    br = max(cells, key=lambda b: b["cx"] + b["cy"])
    tr = max(cells, key=lambda b: b["cx"] - b["cy"])
    bl = min(cells, key=lambda b: b["cx"] - b["cy"])
    corners = {"TL": tl, "TR": tr, "BR": br, "BL": bl}
    if len({id(c) for c in corners.values()}) < 4:
        return None  # blok 1 baris/1 kolom -> sudut-sudut jatuh di sel yg sama, homografi tak terdefinisi
    return corners


def detect_block_corners(gray, corners, search_radius=_CORNER_SEARCH_RADIUS, min_corr=_CORNER_MIN_CORR):
    """Cari posisi ASLI tiap dari 4 sel sudut `corners` (dict TL/TR/BR/BL -> bubble, dari _corner_cells)
    di citra `gray` -- pola sama seperti snap_rows (garis kotak dicocokkan via matchTemplate pada citra
    DoG-highpass) tapi jangkauan jauh lebih lebar, karena di sinilah blok itu "menemukan sudutnya sendiri"
    alih-alih hanya micro-snap di sekitar posisi yang sudah hampir pas. `search_radius`: skalar (sama
    utk x&y) ATAU (radius_x, radius_y) -- kisi rapat spt kolom pilihan A-D (celah horizontal cuma
    2-4px) butuh radius_x jauh lebih sempit dari radius_y supaya jendela pencarian tak pernah menyentuh
    kolom tetangga (lihat _safe_axis_radii). Kembalikan {label: (x, y)} citra -- cuma label yang
    korelasinya cukup yakin (blok bisa dapat &lt;4 -> pemanggil menolak & jatuh ke fallback)."""
    H, W = gray.shape
    hp = _highpass(gray, 1.0, 8.0)
    rx, ry = search_radius if isinstance(search_radius, tuple) else (search_radius, search_radius)
    found = {}
    for label, b in corners.items():
        bw, bh = int(round(b["w"])), int(round(b["h"]))
        x0 = int(round(b["x"])) - rx - 2
        y0 = int(round(b["y"])) - ry - 2
        x1, y1 = x0 + bw + 2 * (rx + 2), y0 + bh + 2 * (ry + 2)
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            continue
        tmpl = _outline(bw, bh, 2)
        res = cv2.matchTemplate(hp[y0:y1, x0:x1], tmpl, cv2.TM_CCOEFF_NORMED)
        _, pk, _, loc = cv2.minMaxLoc(res)
        if pk < min_corr:
            continue
        dx, dy = loc[0] - rx, loc[1] - ry
        found[label] = (b["cx"] + dx, b["cy"] + dy)
    return found


_GAP_SAFETY_MARGIN = 3.0   # px disisakan dari tepi celah antar-kotak, jangan dihabiskan semua
_MIN_AXIS_RADIUS = 3.0


def _safe_axis_radii(cells, cap=_CORNER_SEARCH_RADIUS):
    """Radius pencarian AMAN per sumbu = celah nyata antar-kotak (pitch - lebar/tinggi kotak) dikurangi
    sedikit margin -- BUKAN pecahan tetap dari pitch. Kisi rapat spt LJK ini (kolom pilihan A-D nyaris
    tanpa celah horizontal) membuat radius seragam (dulu 1 nilai utk x&y sekaligus) gampang "mengunci"
    ke kolom tetangga; radius per-sumbu memastikan jendela x tak pernah melewati celah horizontal,
    sementara y (celah antar-baris jauh lebih lega) tetap dapat jangkauan berarti utk koreksi. Radius
    bisa jatuh ke _MIN_AXIS_RADIUS (px) kalau celahnya memang nyaris nol -- itu realitas fisik kisi
    ini, bukan bug; pemanggil (estimate_field_perspective) tetap divalidasi penuh oleh
    _grid_ok/_cell_contrasts spt biasa, jadi koreksi yang salah tetap ditolak."""
    bw = float(np.median([b["w"] for b in cells]))
    bh = float(np.median([b["h"] for b in cells]))
    px, py = _pitch(cells)
    rx = max(_MIN_AXIS_RADIUS, min(cap, (px - bw) - _GAP_SAFETY_MARGIN))
    ry = max(_MIN_AXIS_RADIUS, min(cap, (py - bh) - _GAP_SAFETY_MARGIN))
    return int(round(rx)), int(round(ry))


def estimate_field_perspective(gray, field, search_radius=None, min_final=_MIN_FINAL_CONTRAST):
    """Layer 2: deteksi 4 sudut MILIK BLOK INI SENDIRI (sel bubble paling ujung, dicari independen dg
    jendela lebar -- analog 4 ArUco tapi utk satu kategori isian) lalu hitung transformasi PERSPEKTIF
    penuh dari situ. Lebih kuat dari estimate_field_warp (korelasi kisi kecil, translasi/affine saja,
    jendela ±32px): perspektif menangkap rotasi/skew/skala blok itu sekaligus -- tapi butuh blok cukup
    lebar di 2 sumbu (lihat _corner_cells) dan tiap sudut harus benar-benar ketemu dg korelasi
    meyakinkan. `search_radius=None` (default): dihitung otomatis per-sumbu dari celah antar-kotak
    blok ini sendiri (_safe_axis_radii) -- radius tetap 48px seragam utk x&y TERBUKTI gagal total pd
    kolom pilihan A-D yg celahnya cuma 2-4px (jendela pencarian melewati kolom tetangga, salah kunci
    periode). Boleh dipaksa skalar/tuple eksplisit (dipakai tes). Gagal di salah satu syarat ->
    (None, ...), pemanggil (register_fields) jatuh ke estimate_field_warp sbg jaring pengaman.
    Kembalikan (H 3x3 di koordinat kanvas *lokal crop*, origin (x0,y0), info) atau (None, origin, info)."""
    cells = _cells(field)
    info = {"applied": False, "aligned": False, "method": "perspective"}
    corners = _corner_cells(field)
    if corners is None:
        info["rejected"] = "blok_terlalu_sempit"
        return None, (0, 0), info

    xs = [b["x"] for b in cells]
    ys = [b["y"] for b in cells]
    xe = [b["x"] + b["w"] for b in cells]
    ye = [b["y"] + b["h"] for b in cells]
    x0, y0 = max(0, int(min(xs)) - _MARGIN), max(0, int(min(ys)) - _MARGIN)
    x1, y1 = min(gray.shape[1], int(max(xe)) + _MARGIN), min(gray.shape[0], int(max(ye)) + _MARGIN)
    if x1 - x0 < 40 or y1 - y0 < 40:
        return None, (x0, y0), info

    full_img = gray[y0:y1, x0:x1]
    m0, min_col0, min_row0 = _grid_ok(_cell_contrasts(full_img, cells, x0, y0))
    info.update(contrast0=round(m0, 1))
    if m0 >= _ALIGNED_MEAN and min_col0 >= _MIN_LINE_CONTRAST and min_row0 >= _MIN_LINE_CONTRAST:
        info["aligned"] = True     # garis tercetak sudah jatuh di kisi template: jangan diutak-atik
        return None, (x0, y0), info

    radii = search_radius if search_radius is not None else _safe_axis_radii(cells)
    detected = detect_block_corners(gray, corners, search_radius=radii)
    info["search_radii"] = radii
    if len(detected) < 4:
        info["rejected"] = "sudut_tak_lengkap"
        info["sudut_ditemukan"] = sorted(detected.keys())
        return None, (x0, y0), info

    order = ("TL", "TR", "BR", "BL")
    src = np.array([[corners[k]["cx"], corners[k]["cy"]] for k in order], dtype=np.float32)
    dst = np.array([detected[k] for k in order], dtype=np.float32)
    src_local = src - np.array([x0, y0], dtype=np.float32)
    dst_local = dst - np.array([x0, y0], dtype=np.float32)
    try:
        Hm = cv2.getPerspectiveTransform(src_local, dst_local)
    except cv2.error:
        info["rejected"] = "homografi_gagal"
        return None, (x0, y0), info

    m1, min_col, min_row = _grid_ok(_cell_contrasts(full_img, cells, x0, y0, Hm))
    info.update(contrast1=round(m1, 1), min_col=round(min_col, 1), min_row=round(min_row, 1))
    shift = dst - src
    dx, dy = float(np.mean(shift[:, 0])), float(np.mean(shift[:, 1]))
    # Sama seperti estimate_field_warp: kisi berulang bisa "mendarat" satu periode meleset -- tolak
    # kecuali SETIAP kolom & baris (bukan cuma 4 sudutnya) benar-benar jatuh di garis tercetak.
    line_ok = min_col >= _MIN_LINE_CONTRAST and min_row >= _MIN_LINE_CONTRAST and m1 - m0 >= _MIN_CONTRAST_GAIN
    if line_ok and m1 < min_final and m1 >= _WEAK_FINAL_CONTRAST:
        info.update(weak=True, dx=round(dx, 1), dy=round(dy, 1))
        return Hm, (x0, y0), info
    if m1 < min_final or not line_ok:
        info["rejected"] = "kontras"
        return None, (x0, y0), info
    info.update(applied=True, aligned=True, dx=round(dx, 1), dy=round(dy, 1))
    return Hm, (x0, y0), info


def snap_rows(gray, fields, only=("soal", "kuisioner")):
    """Penyelarasan per baris untuk blok jawaban/kuisioner (kertas fotokopi tidak rata: geser tiap baris berbeda,
    sisa 10-15 px meski blok sudah dikoreksi). Tiap kotak dicocokkan ke garis kotaknya sendiri dalam jendela kecil;
    hasil satu baris = median keempat kotak (kotak bersilang tidak mengacaukan). Baris yang tak konsisten dibiarkan."""
    H, W = gray.shape
    hp = _highpass(gray, 1.0, 8.0)
    out, n_snapped = {}, 0
    for name, fdef in fields.items():
        if not name.lower().startswith(only):
            out[name] = fdef
            continue
        f2 = copy.deepcopy(fdef)
        for it in f2["items"]:
            found = []
            for b in it["bubbles"]:
                bw, bh = int(round(b["w"])), int(round(b["h"]))
                x0, y0 = int(round(b["x"])) - _SNAP_RX - 2, int(round(b["y"])) - _SNAP_RY - 2
                x1, y1 = x0 + bw + 2 * (_SNAP_RX + 2), y0 + bh + 2 * (_SNAP_RY + 2)
                if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
                    continue
                tmpl = _outline(bw, bh, 2)
                res = cv2.matchTemplate(hp[y0:y1, x0:x1], tmpl, cv2.TM_CCOEFF_NORMED)
                _, pk, _, loc = cv2.minMaxLoc(res)
                if pk >= _SNAP_MIN_CORR:
                    found.append((loc[0] - _SNAP_RX, loc[1] - _SNAP_RY))   # (dx, dy) relatif posisi kini
            if len(found) < 2:
                continue
            dxs, dys = [f[0] for f in found], [f[1] for f in found]
            if max(dys) - min(dys) > _SNAP_MAX_SPREAD or max(dxs) - min(dxs) > 2 * _SNAP_MAX_SPREAD:
                continue        # empat kotak tak sepakat -> jangan menebak
            dx, dy = float(np.median(dxs)), float(np.median(dys))
            for b in it["bubbles"]:
                b["cx"] += dx; b["x"] += dx; b["cy"] += dy; b["y"] += dy
            n_snapped += 1
        out[name] = f2
    return out, n_snapped


def register_fields(gray, fields):
    """Koreksi tiap blok. Kembalikan (fields_baru, info_per_blok). fields asli tidak diubah."""
    cand = {}
    infos = {}
    for name, fdef in fields.items():
        min_final = _MIN_FINAL_ANSWER if name.lower().startswith("soal") else _MIN_FINAL_CONTRAST
        try:
            # Layer 2 dulu: blok cari 4 sudutnya sendiri (independen dari blok lain) & hitung
            # perspektif penuh. Cuma jatuh ke korelasi kisi lama (translasi/affine, jendela sempit)
            # kalau blok terlalu sempit utk 4 sudut yg valid, atau sudutnya tak ketemu/tak lolos
            # validasi kontras -- "aligned" (sudah pas, tak perlu koreksi) tak perlu dicoba dua kali.
            W, origin, info = estimate_field_perspective(gray, fdef, min_final=min_final)
            if W is None and not info.get("aligned"):
                W, origin, info = estimate_field_warp(gray, fdef, min_final=min_final)
        except Exception:  # noqa: BLE001 — koreksi hanyalah bantuan; jangan pernah menjatuhkan pemindaian
            W, origin, info = None, (0, 0), {"applied": False, "aligned": False, "error": True}
        infos[name] = info
        cand[name] = (W, origin)
    # konsensus: kandidat lemah diterima bila >= 2 blok lain (kuat/lemah) bergeser searah (distorsi kertas itu halus)
    for name, info in infos.items():
        if not info.get("weak"):
            continue
        sup = sum(1 for o, oi in infos.items()
                  if o != name and (oi.get("applied") or oi.get("weak")) and "dx" in oi
                  and abs(oi["dx"] - info["dx"]) <= _CONSENSUS_DX and abs(oi["dy"] - info["dy"]) <= _CONSENSUS_DY)
        info["dukungan"] = sup
        if sup >= _CONSENSUS_MIN:
            info.update(applied=True, aligned=True)
        else:
            info["rejected"] = "tanpa-konsensus"
            cand[name] = (None, cand[name][1])
    out = {}
    for name, fdef in fields.items():
        W, origin = cand[name]
        out[name] = apply_warp(fdef, W, origin) if (W is not None and infos[name].get("applied")) else fdef
    return out, infos
