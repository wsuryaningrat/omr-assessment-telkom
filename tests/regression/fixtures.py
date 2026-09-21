"""Deterministic input images for scan regression tests (built from LJK.pdf)."""
import os
import cv2
import fitz
import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _blank_page():
    doc = fitz.open(os.path.join(ROOT, "LJK.pdf"))
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(200 / 72.0, 200 / 72.0), alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def _filled_page():
    """Blank LJK with a deterministic pattern of shaded answer bubbles."""
    import json
    img = _blank_page()
    h, w = img.shape[:2]
    with open(os.path.join(ROOT, "templates", "omr_config.json"), encoding="utf-8") as f:
        tpl = json.load(f)
    cw, ch = tpl["canvas"]["width"], tpl["canvas"]["height"]
    sx, sy = w / cw, h / ch
    for fname, fdef in tpl["fields"].items():
        if "soal" not in fname.lower() or "kode" in fname.lower():
            continue
        for n, it in enumerate(fdef.get("items", [])):
            bubbles = it.get("bubbles", [])
            if not bubbles or n % 3 == 2:
                continue
            b = bubbles[n % len(bubbles)]
            r = max(int(b.get("radius", 12) * sx), 6)
            cv2.circle(img, (int(b["cx"] * sx), int(b["cy"] * sy)), r, (30, 30, 30), -1)
    return img


def _phone_photo(img):
    """Simulate a phone photo: rotate slightly, pad, upscale to ~12 MP, JPEG q90."""
    h, w = img.shape[:2]
    canvas = np.full((int(h * 1.15), int(w * 1.15), 3), 200, np.uint8)
    y0, x0 = (canvas.shape[0] - h) // 2, (canvas.shape[1] - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = img
    M = cv2.getRotationMatrix2D((canvas.shape[1] / 2, canvas.shape[0] / 2), 3.0, 1.0)
    canvas = cv2.warpAffine(canvas, M, (canvas.shape[1], canvas.shape[0]), borderValue=(200, 200, 200))
    canvas = cv2.resize(canvas, (3024, 4032), interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def build_cases():
    blank = _blank_page()
    filled = _filled_page()
    return {
        "blank_pdf": blank,
        "filled_pdf": filled,
        "filled_phone": _phone_photo(filled),
    }


KUNCI = {"A": {i: "ABCD"[i % 4] for i in range(1, 76)}}
