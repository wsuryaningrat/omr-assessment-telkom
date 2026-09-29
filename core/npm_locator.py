"""Pencari posisi sebuah blok (NPM, KODE SOAL, FAKULTAS, ...) lewat CNN sendiri -- pengganti/pendamping
ArUco utk kasus potongan halaman yang TIDAK andal (foto tergeser/miring cukup jauh sehingga registrasi kisi
kecil core.field_registration gagal). Alih-alih membutuhkan koordinat pasti, blok digeser+diskalakan pada
kisi kandidat yang lebar, dan posisi yang dipilih adalah yang bacaan CNN-nya (core.cnn_reader) paling
PERCAYA DIRI secara serentak di banyak kolom -- bukan cuma satu kolom (kotak kosong/berisi angka cetak yang
salah-lokasi cenderung menghasilkan skor rendah/berderau di banyak kolom sekaligus, sedangkan posisi yang
benar konsisten tinggi). Bobot CNN dilatih dari kotak NPM (lihat core/cnn_reader.py); dipakai apa adanya utk
KODE SOAL (gaya kotak digitnya identik dgn NPM) dan FAKULTAS (gaya kotak berbeda -- daftar checkbox 1 kolom
dgn label huruf di sebelahnya, belum dilatih khusus, jadi akurasinya belum tentu setinggi NPM/KODE SOAL).

Ini adalah UPAYA TERAKHIR: hanya dipanggil dari _retry_with_registration ketika jalur registrasi kisi kecil
sudah gagal (lihat scanner/service.py). Dua tahap (cepat -> lebar) supaya lembar yang cuma meleset sedikit
tetap murah; hanya lembar yang benar-benar sulit membayar pencarian lebar (~1-3 detik). BATAS WAKTU KERAS
(default _BUDGET_S) menghentikan pencarian dan memakai hasil TERBAIK SEJAUH ITU bila kelamaan -- tak pernah
menunggu tanpa batas; status "timeout" dikembalikan supaya pemanggil bisa mencatatnya.

CATATAN JUJUR (lihat scratch analisis sesi terkait): pada lembar yang distorsinya sangat parah (potongan
"doc_inset" terburuk), pencarian ini SESEKALI mengunci ke posisi yang salah namun skor keyakinannya tetap
tinggi (mis-registrasi periodik: kisi 10x10 berulang bisa "match" di kolom/baris tetangga). Fungsi ini TIDAK
mencoba menghapus risiko itu sepenuhnya -- jaring pengamannya adalah validasi manusia (pengawas) yang sudah
wajib ada di alur aplikasi, dan sekarang pengawas bisa mengoreksi NPM langsung dari dashboard bila salah.
"""
import time

import cv2
import numpy as np

import core.cnn_reader as cr

_FAST_RANGE, _FAST_STEP = 60, 10         # tahap 1: geser sedang, skala tetap (~10-15 dtk)
_WIDE_RANGE, _WIDE_STEP = 120, 25        # tahap 2 (hanya bila tahap 1 tak cukup yakin): geser lebar (~1-2 mnt)
_SCALES = (0.90, 0.96, 1.0, 1.04, 1.10)
_FINE_STEP = 3
_GOOD_ENOUGH_SCORE = 0.55                # tahap 1 dianggap cukup bila skor >= ini -> skip tahap 2 (lebih cepat)
_MIN_TOP, _MIN_MARGIN = 0.02, 0.05       # gerbang keyakinan per-kolom, sama dgn core.cnn_reader
_BUDGET_S = 90.0                         # batas waktu keras (dtk) utk locate(); lihat argumen budget_s
# CATATAN: sempat dicoba mempercepat tahap kasar dgn menilai skor pakai sebagian kolom saja (bukan ke-10).
# Dibatalkan -- pada satu lembar uji itu membuat pencarian gagal menemukan posisi yg SEBELUMNYA (dgn ke-10
# kolom) ketemu sempurna. Fungsi ini jalur upaya-terakhir yg jarang dipanggil, jadi akurasi diutamakan drpd
# kecepatan; ke-10 kolom selalu dipakai utk menilai skor posisi.


def _centers(field):
    return np.array([[b["cx"], b["cy"]] for it in field["items"] for b in it["bubbles"]], np.float32)


def _options(field):
    return [str(b.get("option")) for it in field["items"] for b in it["bubbles"]]


def _batched_probs(gray, centers, offsets, chunk=64):
    """offsets: (K,3) dx,dy,scale. Kembalikan (K, len(centers)) probabilitas.

    Potongan diambil lewat pengindeksan numpy vektor per KELOMPOK kecil dari `offsets` (bukan loop Python
    per sel, dan bukan sekaligus semua kandidat -- itu bisa membengkakkan memori ke GB utk pencarian lebar
    yg kandidatnya ribuan). `chunk` kandidat sekaligus terbukti cepat & hemat memori (lihat scratch analisis).
    """
    P = cr._weights()
    n_cell = len(centers)
    cx0, cy0 = centers[:, 0].mean(), centers[:, 1].mean()
    K = len(offsets)
    W = cr._W
    pad = cv2.copyMakeBorder(gray, W, W, W, W, cv2.BORDER_REPLICATE)
    ph, pw = pad.shape
    off_w = np.arange(W)

    probs = np.empty(K * n_cell, np.float32)
    for c0 in range(0, K, chunk):
        c1 = min(K, c0 + chunk)
        dx, dy, s = offsets[c0:c1, 0], offsets[c0:c1, 1], offsets[c0:c1, 2]      # (k,)
        cx2 = cx0 + (centers[:, 0][None, :] - cx0) * s[:, None] + dx[:, None]    # (k, n_cell)
        cy2 = cy0 + (centers[:, 1][None, :] - cy0) * s[:, None] + dy[:, None]
        x0 = np.rint(cx2).astype(np.int64) - W // 2 + W                         # (k, n_cell)
        y0 = np.rint(cy2).astype(np.int64) - W // 2 + W
        np.clip(x0, 0, pw - W, out=x0)
        np.clip(y0, 0, ph - W, out=y0)

        rows = (y0[..., None] + off_w)[..., None, :]         # (k, n_cell, 1, W)
        cols = (x0[..., None] + off_w)[..., :, None]          # (k, n_cell, W, 1)
        crops = pad[rows, cols].reshape(-1, W, W)             # (k*n_cell, W, W)
        i0 = c0 * n_cell
        probs[i0:i0 + len(crops)] = cr._forward(P, cr._prep(crops))
    return probs.reshape(K, n_cell)


def _block_score(probs_row, n_cols, per_col):
    """Rata2 (top - kedua) tiap kolom -- tinggi & konsisten hanya bila kisi benar2 sejajar dgn kotaknya."""
    total = 0.0
    for c in range(n_cols):
        v = np.sort(probs_row[c * per_col:(c + 1) * per_col])[::-1]
        total += v[0] - (v[1] if len(v) > 1 else 0.0)
    return total / n_cols


def _grid(rng, step):
    return np.arange(-rng, rng + 1, step)


def locate(gray, field, scales=_SCALES, budget_s=_BUDGET_S):
    """Cari (dx,dy,scale,skor) blok `field` yang paling percaya diri. Dua tahap: cepat lalu (bila perlu) lebar,
    lalu satu tahap halus -- semua kolom dipakai di tiap tahap (lihat catatan di atas soal akurasi vs kecepatan).

    `budget_s`: batas waktu keras dlm detik dihitung dari awal panggilan ini. Dicek SEBELUM memulai tahap lebar
    dan tahap halus (bukan di tengah satu tahap -- satu tahap tetap dijalankan utuh sekali dimulai, supaya
    hasilnya tak "terputus" separuh jalan); bila sudah lewat, tahap itu dilewati dan hasil TERBAIK SEJAUH INI
    dipakai. Mengembalikan tuple ke-5 `timed_out: bool` supaya pemanggil tahu pencarian dipotong paksa.
    """
    centers = _centers(field)
    n_cols = len(field["items"])
    per_col = len(field["items"][0]["bubbles"])
    t0 = time.time()
    timed_out = False

    xs, ys = _grid(_FAST_RANGE, _FAST_STEP), _grid(_FAST_RANGE, _FAST_STEP)
    offs = np.array([(x, y, 1.0) for y in ys for x in xs], np.float32)
    scores = np.array([_block_score(p, n_cols, per_col) for p in _batched_probs(gray, centers, offs)])
    bi = scores.argmax()
    best_off, best_score = offs[bi], scores[bi]

    if best_score < _GOOD_ENOUGH_SCORE:
        if time.time() - t0 >= budget_s:
            timed_out = True
        else:
            xs, ys = _grid(_WIDE_RANGE, _WIDE_STEP), _grid(_WIDE_RANGE, _WIDE_STEP)
            offs = np.array([(x, y, s) for s in scales for y in ys for x in xs], np.float32)
            scores = np.array([_block_score(p, n_cols, per_col) for p in _batched_probs(gray, centers, offs)])
            bi = scores.argmax()
            if scores[bi] > best_score:
                best_off, best_score = offs[bi], scores[bi]

    if time.time() - t0 >= budget_s:
        timed_out = True
        best_probs = _batched_probs(gray, centers, best_off[None, :])[0]
        return tuple(best_off), float(best_score), best_probs, time.time() - t0, timed_out

    bx, by, bs = best_off
    xs2 = np.arange(bx - _FAST_STEP, bx + _FAST_STEP + 1, _FINE_STEP)
    ys2 = np.arange(by - _FAST_STEP, by + _FAST_STEP + 1, _FINE_STEP)
    scales2 = sorted({round(bs - 0.02, 2), round(bs, 2), round(bs + 0.02, 2)})
    offs2 = np.array([(x, y, s) for s in scales2 for y in ys2 for x in xs2], np.float32)
    probs2 = _batched_probs(gray, centers, offs2)
    scores2 = np.array([_block_score(p, n_cols, per_col) for p in probs2])
    bi2 = scores2.argmax()
    best_off, best_score, best_probs = offs2[bi2], scores2[bi2], probs2[bi2]

    return tuple(best_off), float(best_score), best_probs, time.time() - t0, timed_out


def read(gray, field, budget_s=_BUDGET_S):
    """Cari posisi lalu baca digit/opsi per kolom. Kembalikan (dict{kolom: opsi} utk kolom yg lolos gerbang,
    (dx,dy,scale), skor_blok, timed_out). Kolom yang tak lolos gerbang tidak muncul di hasil (dibiarkan spt
    semula). `timed_out=True` berarti hasil dipakai dari batas waktu, bukan pencarian yg tuntas -- pemanggil
    sebaiknya mencatatnya (bukan diam-diam dianggap normal)."""
    if cr._weights() is None:
        return {}, (0.0, 0.0, 1.0), 0.0, False
    (dx, dy, s), block_score, probs, _dt, timed_out = locate(gray, field, budget_s=budget_s)
    opts = _options(field)
    n_cols = len(field["items"])
    per_col = len(field["items"][0]["bubbles"])
    out = {}
    for c in range(n_cols):
        v = probs[c * per_col:(c + 1) * per_col]
        order = np.argsort(-v)
        top, second = float(v[order[0]]), float(v[order[1]]) if per_col > 1 else 0.0
        if top >= _MIN_TOP and (top - second) >= _MIN_MARGIN:
            out[c] = opts[c * per_col + int(order[0])]
    return out, (float(dx), float(dy), float(s)), block_score, timed_out
