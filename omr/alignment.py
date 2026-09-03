"""
Alignment and Quality Gate module.
Handles marker detection, perspective transformation, and image quality checks.
"""

from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import cv2


def calculate_blur_score(gray_img: np.ndarray) -> float:
    """Calculate the Laplacian variance as a blur metric (higher = sharper)."""
    return float(cv2.Laplacian(gray_img, cv2.CV_64F).var())


def check_quality_gate(
    img: np.ndarray,
    min_width: int = 600,
    min_height: int = 800,
    blur_threshold: float = 25.0,
    min_brightness: float = 20.0,
    max_brightness: float = 248.0,
    min_contrast: float = 15.0
) -> Tuple[bool, str]:
    """
    Perform deterministic quality checks on raw input image before alignment.
    Returns (is_passed, failure_reason).
    """
    if img is None or img.size == 0:
        return False, "EMPTY_IMAGE"

    h, w = img.shape[:2]
    if w < min_width or h < min_height:
        return False, f"LOW_RESOLUTION_{w}x{h}"

    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    # Blur check
    blur_score = calculate_blur_score(gray)
    if blur_score < blur_threshold:
        return False, f"IMAGE_TOO_BLURRY_SCORE_{blur_score:.1f}"

    # Brightness & contrast checks
    mean_val = float(np.mean(gray))
    std_val = float(np.std(gray))

    if mean_val < min_brightness:
        return False, f"IMAGE_TOO_DARK_{mean_val:.1f}"
    if mean_val > max_brightness:
        return False, f"IMAGE_OVEREXPOSED_{mean_val:.1f}"
    if std_val < min_contrast:
        return False, f"LOW_CONTRAST_{std_val:.1f}"

    return True, "PASSED"


def detect_aruco_markers(
    gray_img: np.ndarray,
    marker_dict_name: str = "DICT_4X4_50"
) -> Dict[int, np.ndarray]:
    """
    Detect ArUco markers and return a dictionary mapping marker_id -> center_point (x, y).
    """
    detected_markers: Dict[int, np.ndarray] = {}

    try:
        dict_id = getattr(cv2.aruco, marker_dict_name, cv2.aruco.DICT_4X4_50)
        aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)

        if hasattr(cv2.aruco, "ArucoDetector"):
            parameters = cv2.aruco.DetectorParameters()
            parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
            corners, ids, _ = detector.detectMarkers(gray_img)
        else:
            parameters = cv2.aruco.DetectorParameters_create()
            parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            corners, ids, _ = cv2.aruco.detectMarkers(gray_img, aruco_dict, parameters=parameters)

        if ids is not None and len(ids) > 0:
            for i, marker_id in enumerate(ids.flatten()):
                c = corners[i][0]  # 4 corner points of marker
                center_x = float(np.mean(c[:, 0]))
                center_y = float(np.mean(c[:, 1]))
                detected_markers[int(marker_id)] = np.array([center_x, center_y], dtype=np.float32)
    except Exception:
        pass

    return detected_markers


def detect_corner_fiducial_boxes(
    gray_img: np.ndarray
) -> Optional[np.ndarray]:
    """
    Detect 4 solid black corner marker squares / fiducials on the page.
    Returns sorted 4 corners: Top-Left, Top-Right, Bottom-Right, Bottom-Left.
    """
    h, w = gray_img.shape[:2]

    # Binarize to find dark rectangular marks
    _, thresh = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Morphological filtering to keep solid blocks
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    valid_boxes = []
    min_area = (w * h) * 0.0001   # Min 0.01% of page
    max_area = (w * h) * 0.02     # Max 2% of page

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if min_area < area < max_area:
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
            x, y, bw, bh = cv2.boundingRect(approx)
            aspect_ratio = float(bw) / bh if bh > 0 else 0

            if 0.6 <= aspect_ratio <= 1.6 and len(approx) in [4, 5, 6]:
                M = cv2.moments(cnt)
                if M["m00"] > 0:
                    cx = float(M["m10"] / M["m00"])
                    cy = float(M["m01"] / M["m00"])
                    valid_boxes.append((cx, cy, area))

    if len(valid_boxes) < 4:
        return None

    quadrants = {"TL": [], "TR": [], "BR": [], "BL": []}
    mid_x, mid_y = w / 2.0, h / 2.0

    for cx, cy, area in valid_boxes:
        if cx < mid_x and cy < mid_y:
            quadrants["TL"].append((cx, cy, area, cx**2 + cy**2))
        elif cx >= mid_x and cy < mid_y:
            quadrants["TR"].append((cx, cy, area, (w - cx)**2 + cy**2))
        elif cx >= mid_x and cy >= mid_y:
            quadrants["BR"].append((cx, cy, area, (w - cx)**2 + (h - cy)**2))
        else:
            quadrants["BL"].append((cx, cy, area, cx**2 + (h - cy)**2))

    corners = []
    for quad_name in ["TL", "TR", "BR", "BL"]:
        candidates = quadrants[quad_name]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[3])
        corners.append([candidates[0][0], candidates[0][1]])

    return np.array(corners, dtype=np.float32)


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order coordinates consistently: [Top-Left, Top-Right, Bottom-Right, Bottom-Left]."""
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def align_image(
    raw_img: np.ndarray,
    template: Dict[str, Any]
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Align input image to the canonical canvas using template registration markers.
    Returns (aligned_image, alignment_metadata).
    """
    meta: Dict[str, Any] = {
        "status": "FAILED",
        "method": "NONE",
        "quality_gate": "FAILED",
        "blur_score": 0.0,
        "src_corners": None,
        "reason": ""
    }

    if raw_img is None:
        meta["reason"] = "IMAGE_IS_NONE"
        return None, meta

    # Quality Gate Check
    passed, reason = check_quality_gate(raw_img)
    meta["quality_gate"] = reason
    if not passed:
        meta["reason"] = f"QUALITY_GATE_FAILED_{reason}"
        return None, meta

    gray = cv2.cvtColor(raw_img, cv2.COLOR_BGR2GRAY) if len(raw_img.shape) == 3 else raw_img
    meta["blur_score"] = calculate_blur_score(gray)

    canonical_w = int(template.get("canonical_width", 1654))
    canonical_h = int(template.get("canonical_height", 2339))
    marker_cfg = template.get("markers", {})
    target_corners = np.array(marker_cfg.get("target_corners", [
        [60, 60],
        [canonical_w - 60, 60],
        [canonical_w - 60, canonical_h - 60],
        [60, canonical_h - 60]
    ]), dtype=np.float32)

    src_corners: Optional[np.ndarray] = None
    method = "NONE"

    # Strategy 1: ArUco Marker Alignment
    expected_ids = marker_cfg.get("marker_ids", [0, 1, 2, 3])
    dict_name = marker_cfg.get("aruco_dict", "DICT_4X4_50")
    detected_aruco = detect_aruco_markers(gray, dict_name)

    if all(mid in detected_aruco for mid in expected_ids):
        src_corners = np.array([detected_aruco[mid] for mid in expected_ids], dtype=np.float32)
        method = "ARUCO_4_MARKERS"
    else:
        # Strategy 2: 4 Solid Corner Fiducial Squares
        corner_boxes = detect_corner_fiducial_boxes(gray)
        if corner_boxes is not None and len(corner_boxes) == 4:
            src_corners = corner_boxes
            method = "CORNER_FIDUCIAL_BOXES"
        else:
            # Strategy 3: Check if input image is already canonical direct image
            h, w = gray.shape[:2]
            if abs(w - canonical_w) < 20 and abs(h - canonical_h) < 20:
                aligned = cv2.resize(raw_img, (canonical_w, canonical_h), interpolation=cv2.INTER_AREA)
                meta["status"] = "OK"
                meta["method"] = "CANONICAL_DIRECT"
                meta["src_corners"] = target_corners.tolist()
                return aligned, meta

    if src_corners is None or len(src_corners) != 4:
        meta["status"] = "FAILED"
        meta["reason"] = "ALIGNMENT_FAILED_MARKERS_NOT_FOUND"
        return None, meta

    # Compute homography perspective transform
    try:
        M = cv2.getPerspectiveTransform(src_corners, target_corners)
        aligned = cv2.warpPerspective(
            raw_img,
            M,
            (canonical_w, canonical_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255)
        )

        meta["status"] = "OK"
        meta["method"] = method
        meta["src_corners"] = src_corners.tolist()
        return aligned, meta
    except Exception as e:
        meta["status"] = "FAILED"
        meta["reason"] = f"TRANSFORM_ERROR_{str(e)}"
        return None, meta
