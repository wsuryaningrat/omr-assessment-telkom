import cv2
import numpy as np

def enhance_camscanner(img_bgr: np.ndarray, mode: str = "magic_color") -> np.ndarray:
    """
    CamScanner-grade image filter (inspired by addyosmani/scan & modern OpenCV pipelines):
    - mode='magic_color': Background whitening + local shadow removal + CLAHE contrast boost.
    - mode='clean_bw': Crisp adaptive binary thresholding (black marks on pure white paper).
    - mode='sharp': Unsharp masking for high-contrast optical inspection.
    """
    if img_bgr is None:
        return None

    if mode == "magic_color":
        rgb_planes = cv2.split(img_bgr)
        result_planes = []
        for plane in rgb_planes:
            dilated = cv2.dilate(plane, np.ones((7, 7), np.uint8))
            bg_blur = cv2.medianBlur(dilated, 21)
            diff = 255 - cv2.absdiff(plane, bg_blur)
            norm = cv2.normalize(diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)
            result_planes.append(norm)
        result = cv2.merge(result_planes)
        # Levels adjustment (black/white point) — pushes near-white to white, near-black to black
        result = cv2.convertScaleAbs(result, alpha=1.15, beta=-15)
        lab = cv2.cvtColor(result, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l_clahe = clahe.apply(l)
        enhanced = cv2.cvtColor(cv2.merge([l_clahe, a, b]), cv2.COLOR_LAB2BGR)
        return enhanced
    elif mode == "clean_bw":
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        smooth = cv2.GaussianBlur(gray, (5, 5), 0)
        bw = cv2.adaptiveThreshold(smooth, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 10)
        return cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)
    elif mode == "sharp":
        gaussian = cv2.GaussianBlur(img_bgr, (0, 0), 2.0)
        unsharp = cv2.addWeighted(img_bgr, 1.5, gaussian, -0.5, 0)
        return unsharp
    return img_bgr
