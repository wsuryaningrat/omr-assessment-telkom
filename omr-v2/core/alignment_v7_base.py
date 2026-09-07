"""Robust document alignment and ArUco corner detection.

Pipeline:
    1. Create one bounded detection-resolution copy.
    2. Detect the physical paper once.
    3. Rough-warp only for corner-anchor detection.
    4. Detect ArUco only in the four corner ROIs.
    5. Use RegMark INNER corners when the sheet uses registration marks.
    6. Fall back to the physical paper boundary only if anchors fail.
    7. Perform the final perspective warp from the original image.

The public function signatures are kept compatible with the previous module.
"""

import cv2
import numpy as np


FAST_ARUCO_DICTS = [
    ("DICT_4X4_50", cv2.aruco.DICT_4X4_50),
    ("DICT_4X4_250", cv2.aruco.DICT_4X4_250),
    ("DICT_5X5_50", cv2.aruco.DICT_5X5_50),
    ("DICT_6X6_50", cv2.aruco.DICT_6X6_50),
    ("DICT_APRILTAG_36h11", cv2.aruco.DICT_APRILTAG_36h11),
]

FALLBACK_DICTS = [
    ("DICT_4X4_100", cv2.aruco.DICT_4X4_100),
    ("DICT_4X4_1000", cv2.aruco.DICT_4X4_1000),
    ("DICT_5X5_100", cv2.aruco.DICT_5X5_100),
    ("DICT_5X5_250", cv2.aruco.DICT_5X5_250),
    ("DICT_6X6_100", cv2.aruco.DICT_6X6_100),
    ("DICT_6X6_250", cv2.aruco.DICT_6X6_250),
    ("DICT_ARUCO_ORIGINAL", cv2.aruco.DICT_ARUCO_ORIGINAL),
]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def unrotate_point(pt, orig_shape, angle):
    orig_h, orig_w = orig_shape[:2]
    rx, ry = float(pt[0]), float(pt[1])
    if angle == 90:
        return np.array([ry, orig_h - 1.0 - rx], dtype=np.float32)
    if angle == 180:
        return np.array([orig_w - 1.0 - rx, orig_h - 1.0 - ry], dtype=np.float32)
    if angle in (270, -90):
        return np.array([orig_w - 1.0 - ry, rx], dtype=np.float32)
    return np.array([rx, ry], dtype=np.float32)


def get_marker_rotation(corners):
    v = corners[1] - corners[0]
    angle = np.degrees(np.arctan2(v[1], v[0]))
    if -45 <= angle < 45:
        return 0
    if 45 <= angle < 135:
        return 90
    if angle >= 135 or angle < -135:
        return 180
    return 270


def order_points(pts, target_w=1700, target_h=2400):
    """Return points ordered TL, TR, BR, BL without relying only on x/y."""
    pts = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
    if len(pts) != 4:
        raise ValueError("order_points expects exactly 4 points")

    # Convex hull gives stable cyclic ordering even for strong perspective.
    hull = cv2.convexHull(pts).reshape(-1, 2)
    if len(hull) == 4:
        center = hull.mean(axis=0)
        angles = np.arctan2(hull[:, 1] - center[1], hull[:, 0] - center[0])
        hull = hull[np.argsort(angles)]
        # Start at top-left by minimum x+y.
        hull = np.roll(hull, -int(np.argmin(hull.sum(axis=1))), axis=0)
        # Ensure clockwise TL -> TR -> BR -> BL in image coordinates.
        # Do not use np.cross() here: NumPy 2.x removed scalar 2-D cross
        # products and raises ValueError. Compute the z-component explicitly.
        v1 = hull[1] - hull[0]
        v2 = hull[2] - hull[1]
        cross_z = float(v1[0] * v2[1] - v1[1] * v2[0])
        if cross_z < 0:
            hull = hull[[0, 3, 2, 1]]
        rect = hull.astype(np.float32)
    else:
        s = pts.sum(axis=1)
        d = np.diff(pts, axis=1).ravel()
        rect = np.array([
            pts[np.argmin(s)], pts[np.argmin(d)],
            pts[np.argmax(s)], pts[np.argmax(d)]
        ], dtype=np.float32)

    # For portrait targets, rotate an accidental landscape ordering.
    top_w = np.linalg.norm(rect[1] - rect[0])
    bot_w = np.linalg.norm(rect[2] - rect[3])
    left_h = np.linalg.norm(rect[3] - rect[0])
    right_h = np.linalg.norm(rect[2] - rect[1])
    avg_w = (top_w + bot_w) / 2.0
    avg_h = (left_h + right_h) / 2.0
    if target_h > target_w and avg_w > avg_h * 1.10:
        rect = np.roll(rect, -1, axis=0)
    return rect.astype(np.float32)


def _quad_quality(pts, image_shape=None):
    """Return a geometry quality score in [0, 1]."""
    try:
        p = order_points(pts)
    except Exception:
        return 0.0
    area = abs(cv2.contourArea(p.reshape(-1, 1, 2)))
    if area <= 1:
        return 0.0
    if image_shape is not None:
        h, w = image_shape[:2]
        ratio = area / float(max(1, w * h))
        if ratio < 0.03:
            return 0.0
    edges = [np.linalg.norm(p[(i + 1) % 4] - p[i]) for i in range(4)]
    if min(edges) < 5:
        return 0.0
    opp1 = min(edges[0], edges[2]) / max(edges[0], edges[2])
    opp2 = min(edges[1], edges[3]) / max(edges[1], edges[3])
    return float(np.clip((opp1 + opp2) / 2.0, 0.0, 1.0))


def _rotate_candidate(image, angle):
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


# ---------------------------------------------------------------------------
# ArUco detection
# ---------------------------------------------------------------------------

def make_fast_detector(dict_val, min_perimeter=0.005, step=4):
    """Detector tuned for small fiducials after rough document cropping."""
    dictionary = cv2.aruco.getPredefinedDictionary(dict_val)
    parameters = cv2.aruco.DetectorParameters()
    parameters.minMarkerPerimeterRate = float(min_perimeter)
    parameters.maxMarkerPerimeterRate = 4.0
    parameters.adaptiveThreshWinSizeMin = 3
    parameters.adaptiveThreshWinSizeMax = 23
    parameters.adaptiveThreshWinSizeStep = min(step, 10)
    parameters.minCornerDistanceRate = 0.01
    parameters.minDistanceToBorder = 1
    parameters.polygonalApproxAccuracyRate = 0.03
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    parameters.cornerRefinementWinSize = 5
    parameters.cornerRefinementMaxIterations = 50
    parameters.cornerRefinementMinAccuracy = 0.03
    return cv2.aruco.ArucoDetector(dictionary, parameters)


def _aruco_variants(gray, include_enhanced=True):
    """Return a deliberately small detection cascade.

    The first pass is always the raw grayscale image. Enhanced variants are
    only used after the cheap pass fails, avoiding several expensive
    detectMarkers() calls on every image.
    """
    variants = [("gray", gray)]
    if include_enhanced:
        clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
        variants.append(("clahe", clahe.apply(gray)))
    return variants


def _detect_at_scale(gray, detector, scale):
    h, w = gray.shape[:2]
    if abs(scale - 1.0) < 1e-6:
        work = gray
    else:
        work = cv2.resize(gray, (max(32, int(round(w * scale))),
                                 max(32, int(round(h * scale)))),
                          interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    corners, ids, _ = detector.detectMarkers(work)
    if ids is None:
        return []
    inv = 1.0 / scale
    return [(int(mid), corners[i][0].astype(np.float32) * inv) for i, mid in enumerate(ids.flatten())]


def _merge_marker_detections(detections, allowed_ids):
    """Keep the best/largest detection per ID."""
    result = {}
    for mid, pts in detections:
        if mid not in allowed_ids:
            continue
        area = abs(cv2.contourArea(pts.reshape(-1, 1, 2)))
        if area < 4:
            continue
        if mid not in result:
            result[mid] = pts
        else:
            old_area = abs(cv2.contourArea(result[mid].reshape(-1, 1, 2)))
            if area > old_area:
                result[mid] = pts
    return result


def _predict_missing_center(marker_map, expected):
    """Predict missing marker center using an affine map from the three known IDs."""
    labels = ["TL", "TR", "BR", "BL"]
    found = [lab for lab in labels if expected[lab] in marker_map]
    missing = [lab for lab in labels if expected[lab] not in marker_map]
    if len(found) != 3 or len(missing) != 1:
        return None, None

    canonical = {
        "TL": np.array([0.0, 0.0], np.float32),
        "TR": np.array([1.0, 0.0], np.float32),
        "BR": np.array([1.0, 1.0], np.float32),
        "BL": np.array([0.0, 1.0], np.float32),
    }
    src = np.array([np.mean(marker_map[expected[l]], axis=0) for l in found], dtype=np.float32)
    dst = np.array([canonical[l] for l in found], dtype=np.float32)
    # Map canonical -> image using the 3 known centers.
    A = cv2.getAffineTransform(dst, src)
    pred = cv2.transform(np.array([[canonical[missing[0]]]], dtype=np.float32), A)[0, 0]
    return missing[0], pred


def _local_marker_search(gray, detector, marker_id, center, radius):
    h, w = gray.shape[:2]
    cx, cy = map(int, np.round(center))
    r = int(max(40, radius))
    x1, x2 = max(0, cx - r), min(w, cx + r)
    y1, y2 = max(0, cy - r), min(h, cy + r)
    patch = gray[y1:y2, x1:x2]
    if patch.size == 0:
        return None

    candidates = [patch]
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(6, 6))
    candidates.append(clahe.apply(patch))
    candidates.append(cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])

    best = None
    best_score = -1.0
    for candidate in candidates:
        corners, ids, _ = detector.detectMarkers(candidate)
        if ids is None:
            continue
        for i, mid in enumerate(ids.flatten()):
            if int(mid) != int(marker_id):
                continue
            pts = corners[i][0].astype(np.float32) + np.array([x1, y1], dtype=np.float32)
            c = pts.mean(axis=0)
            dist = np.linalg.norm(c - np.asarray(center, np.float32))
            area = abs(cv2.contourArea(pts.reshape(-1, 1, 2)))
            score = (area ** 0.5) / (1.0 + dist)
            if score > best_score:
                best_score = score
                best = pts
    return best


def recover_missing_corner(marker_map, exp_c_ids, full_gray,
                           dict_val=cv2.aruco.DICT_4X4_50):
    """Recover a missing fourth marker by affine prediction + local detection.

    Synthesis is used only as a last resort and only when the three detected
    markers have a coherent geometry. This preserves the old API while making
    the normal path depend on a real marker whenever possible.
    """
    if len(marker_map) != 3:
        return marker_map

    missing_lbl, predicted = _predict_missing_center(marker_map, exp_c_ids)
    if predicted is None:
        return marker_map
    missing_id = exp_c_ids[missing_lbl]

    detector = make_fast_detector(dict_val, min_perimeter=0.003)
    marker_sizes = []
    for pts in marker_map.values():
        marker_sizes.append(np.sqrt(abs(cv2.contourArea(pts.reshape(-1, 1, 2)))))
    radius = int(np.clip(np.median(marker_sizes) * 5.0, 80, 500))

    found = _local_marker_search(full_gray, detector, missing_id, predicted, radius)
    if found is not None:
        marker_map[missing_id] = found
        return marker_map

    # Last-resort synthetic corners. This is deliberately conservative.
    ref = next(iter(marker_map.values()))
    ref_center = ref.mean(axis=0)
    synthetic = ref - ref_center + predicted
    marker_map[missing_id] = synthetic.astype(np.float32)
    return marker_map


def find_aruco_markers(image, dict_name=None, expected_ids=None, crop_mode="inner"):
    """Fast ArUco corner detection.

    ArUco markers are expected near the four page corners, so detection is
    restricted to four corner ROIs instead of scanning the entire page. This
    both speeds detection and prevents answer bubbles / text from becoming
    false positives. The default crop point is explicitly the INNER marker
    vertex, i.e. the vertex pointing toward the page center.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    h, w = gray.shape[:2]

    dict_val = cv2.aruco.DICT_4X4_50
    best_dict_name = "DICT_4X4_50"
    if dict_name and dict_name not in ("auto", "") and hasattr(cv2.aruco, dict_name):
        dict_val = getattr(cv2.aruco, dict_name)
        best_dict_name = dict_name

    expected = expected_ids or {"TL": 0, "TR": 1, "BR": 3, "BL": 2}
    allowed = set(expected.values())
    detector = make_fast_detector(dict_val, min_perimeter=0.008, step=10)

    # Corner ROIs: enough area for a marker that sits slightly inward from the
    # page edge, but small enough to exclude most OMR content.
    roi_w = max(180, int(round(w * 0.24)))
    roi_h = max(180, int(round(h * 0.24)))
    rois = {
        "TL": (0, 0, roi_w, roi_h),
        "TR": (w - roi_w, 0, w, roi_h),
        "BR": (w - roi_w, h - roi_h, w, h),
        "BL": (0, h - roi_h, roi_w, h),
    }

    marker_map = {}
    for label, (x1, y1, x2, y2) in rois.items():
        roi = gray[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        # Cheap raw pass first. Only if it fails, try CLAHE once.
        passes = (roi,)
        for pass_index, candidate in enumerate(passes):
            corners, ids, _ = detector.detectMarkers(candidate)
            if ids is None:
                if pass_index == 0:
                    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                    candidate = clahe.apply(roi)
                    corners, ids, _ = detector.detectMarkers(candidate)
                else:
                    continue
            if ids is None:
                continue
            for i, mid in enumerate(ids.flatten()):
                mid = int(mid)
                if mid not in allowed:
                    continue
                pts = corners[i][0].astype(np.float32) + np.array([x1, y1], np.float32)
                area = abs(cv2.contourArea(pts.reshape(-1, 1, 2)))
                if area >= 4:
                    marker_map[mid] = pts
            if marker_map.get(expected[label]) is not None:
                break

    if len(marker_map) == 3:
        # Recovery uses the same image but only after the fast 4-ROI pass.
        marker_map = recover_missing_corner(marker_map, expected, gray, dict_val=dict_val)

    if len(marker_map) < 4:
        return None, None, best_dict_name, (
            f"ArUco: Ditemukan {len(marker_map)}/4 marker sudut "
            f"{list(marker_map.keys())} ({best_dict_name})."
        )

    # Explicit INNER-corner selection. For each marker, choose the vertex whose
    # direction from the marker center points most strongly toward the page center.
    marker_centers = {
        lbl: marker_map[expected[lbl]].mean(axis=0).astype(np.float32)
        for lbl in ("TL", "TR", "BR", "BL")
    }
    page_center = np.mean(np.vstack(list(marker_centers.values())), axis=0)
    crop_pts = []
    for lbl in ("TL", "TR", "BR", "BL"):
        pts = marker_map[expected[lbl]].astype(np.float32)
        if crop_mode == "center":
            pt = pts.mean(axis=0)
        else:
            direction = page_center - marker_centers[lbl]
            norm = float(np.linalg.norm(direction))
            if norm < 1e-6:
                pt = pts[np.argmin(np.linalg.norm(pts - page_center, axis=1))]
            else:
                unit = direction / norm
                projection = (pts - marker_centers[lbl]) @ unit
                # INNER is the default. OUTER remains only for explicit legacy calls.
                pt = pts[np.argmin(projection) if crop_mode == "outer" else np.argmax(projection)]
        crop_pts.append(pt)

    ordered = np.asarray(crop_pts, dtype=np.float32)
    if _quad_quality(ordered, gray.shape) < 0.20:
        return None, None, best_dict_name, "ArUco: geometri marker tidak valid."

    return ordered, {lbl: expected[lbl] for lbl in ("TL", "TR", "BR", "BL")}, best_dict_name, "DETECTED"


# ---------------------------------------------------------------------------
# Physical paper boundary / rough crop
# ---------------------------------------------------------------------------

def _find_best_document_quad(image):
    h, w = image.shape[:2]
    max_dim = 1200
    scale = min(1.0, max_dim / float(max(h, w)))
    small = image if scale >= 0.999 else cv2.resize(
        image, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA
    )
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small.copy()

    # Multiple edge variants improve detection under shadows / warm paper.
    gray_blur = cv2.GaussianBlur(gray, (5, 5), 0)
    variants = [
        cv2.Canny(gray_blur, 25, 90),
        cv2.Canny(gray_blur, 50, 150),
    ]
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray_blur)
    variants.append(cv2.Canny(clahe, 30, 110))

    best = None
    best_score = -1.0
    img_area = float(gray.shape[0] * gray.shape[1])
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    for edges in variants:
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:40]

        for cnt in contours:
            area = cv2.contourArea(cnt)
            ratio = area / img_area
            if ratio < 0.10:
                continue
            peri = cv2.arcLength(cnt, True)
            for eps in (0.015, 0.02, 0.03, 0.04):
                approx = cv2.approxPolyDP(cnt, eps * peri, True)
                if len(approx) != 4 or not cv2.isContourConvex(approx):
                    continue
                pts = approx.reshape(4, 2).astype(np.float32) / float(scale)
                pts = order_points(pts)
                q = _quad_quality(pts, image.shape)
                score = ratio * (0.65 + 0.35 * q)
                if score > best_score:
                    best_score = score
                    best = pts
                break
    return best


def find_document_corners(image, min_area_ratio=0.18, target_w=1700, target_h=2400):
    """Find the physical sheet before looking for fiducial markers."""
    h, w = image.shape[:2]
    quad = _find_best_document_quad(image)

    if quad is not None:
        area_ratio = abs(cv2.contourArea(quad.reshape(-1, 1, 2))) / float(max(1, w * h))
        if area_ratio >= min(0.08, min_area_ratio * 0.55):
            return order_points(quad, target_w=target_w, target_h=target_h), "DETECTED"

    full = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    return full, "DETECTED (Batas Halaman)"


def _rough_warp(image, doc_corners, width=1800, height=2500):
    warped, M = perspective_warp(image, doc_corners, width, height, interpolation=cv2.INTER_LINEAR)
    try:
        inv_M = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        inv_M = None
    return warped, M, inv_M


def _map_points_back(points, inv_M):
    if points is None or inv_M is None:
        return None
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(pts, inv_M).reshape(-1, 2)
    return mapped.astype(np.float32)


# ---------------------------------------------------------------------------
# Legacy RegMark fallback
# ---------------------------------------------------------------------------

def find_regmarks(image, target_w=1700, target_h=2400, crop_mode="inner", doc_corners=None):
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    if doc_corners is None:
        doc_corners, _ = find_document_corners(image, target_w=target_w, target_h=target_h)
    doc_corners = order_points(doc_corners, target_w=target_w, target_h=target_h)
    center = doc_corners.mean(axis=0)

    # Work on a normalized rough page so marker size thresholds are resolution independent.
    rough, _, _ = _rough_warp(image, doc_corners, 1200, 1700)
    rg = cv2.cvtColor(rough, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(rg, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    pts_out = []
    found = 0
    rh, rw = rg.shape
    rough_center = np.array([rw / 2.0, rh / 2.0], dtype=np.float32)

    for corner in ([0, 0], [rw - 1, 0], [rw - 1, rh - 1], [0, rh - 1]):
        # Search a broad 35% corner wedge.
        cx, cy = map(int, corner)
        vx, vy = rough_center - np.asarray(corner, np.float32)
        x2 = int(np.clip(cx + vx * 0.35, 0, rw - 1))
        y2 = int(np.clip(cy + vy * 0.35, 0, rh - 1))
        x1, xx = sorted((cx, x2))
        y1, yy = sorted((cy, y2))
        roi = thresh[y1:yy + 1, x1:xx + 1]
        cnts, _ = cv2.findContours(roi, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        best_dist = float("inf")
        for c in cnts:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bh <= 0 or bw <= 0:
                continue
            ar = bw / float(bh)
            area = cv2.contourArea(c)
            fill = area / float(bw * bh)
            if not (0.55 <= ar <= 1.8 and 30 <= area <= rw * rh * 0.02 and fill >= 0.55):
                continue
            cand = c.reshape(-1, 2).astype(np.float32) + np.array([x1, y1], np.float32)
            cc = cand.mean(axis=0)
            d = np.linalg.norm(cc - np.asarray(corner, np.float32))
            if d < best_dist:
                best_dist = d
                best = cand
        if best is None:
            pts_out.append(np.asarray(corner, np.float32))
            continue
        found += 1
        d = np.linalg.norm(best - rough_center, axis=1)
        pts_out.append(best[np.argmin(d)] if crop_mode == "inner" else best[np.argmax(d)] if crop_mode == "outer" else best.mean(axis=0))

    if found == 4:
        # Convert normalized rough coordinates back to original image.
        scale_x = (rw - 1) / max(1.0, rw - 1)
        scale_y = (rh - 1) / max(1.0, rh - 1)
        # Directly use the homography from document corners to rough page.
        src = np.array([[0, 0], [rw - 1, 0], [rw - 1, rh - 1], [0, rh - 1]], np.float32)
        H = cv2.getPerspectiveTransform(src, doc_corners.astype(np.float32))
        mapped = _map_points_back(np.array(pts_out, np.float32), H)
        return order_points(mapped, target_w=target_w, target_h=target_h), "DETECTED"

    return doc_corners, "FALLBACK_DOC_CORNERS"


# ---------------------------------------------------------------------------
# Final image processing
# ---------------------------------------------------------------------------

def _odd_int(value, minimum=3):
    value = max(minimum, int(round(value)))
    return value if value % 2 == 1 else value + 1


def enhance_scan_gray(gray, strength=1.0):
    """Create a scanner-like grayscale image without hard binarization.

    The goal is similar to consumer document scanners: flatten uneven page
    illumination, push paper toward white, strengthen dark print/boxes, and
    add mild local sharpness. We intentionally avoid a full binary threshold,
    because OMR marks and thin box borders can be damaged by aggressive
    thresholding.
    """
    if gray is None or gray.size == 0:
        return gray

    gray = np.asarray(gray, dtype=np.uint8)
    h, w = gray.shape[:2]

    # 1) Suppress sensor noise while keeping thin printed edges.
    den = cv2.GaussianBlur(gray, (3, 3), 0)

    # 2) Estimate slow illumination/background variation.
    #    This is the key "scanner" step that removes table shadows and page
    #    gradients before contrast is applied.
    sigma = max(12.0, min(h, w) * 0.035)
    bg = cv2.GaussianBlur(den, (0, 0), sigmaX=sigma, sigmaY=sigma)
    bg_f = np.maximum(bg.astype(np.float32), 12.0)
    flat = np.clip(den.astype(np.float32) / bg_f * 230.0, 0, 255)
    flat = flat.astype(np.uint8)

    # 3) Robust global contrast stretch. Percentiles prevent a few black
    #    bubbles/registration marks from dictating the range.
    lo, hi = np.percentile(flat, [1.0, 99.0])
    if hi > lo + 12:
        stretched = np.clip((flat.astype(np.float32) - lo) * 250.0 / (hi - lo), 0, 255).astype(np.uint8)
    else:
        stretched = flat

    # 4) Mild local contrast. Keep clipLimit modest so the printed grid does
    #    not become noisy.
    clahe = cv2.createCLAHE(clipLimit=1.7 + 0.3 * float(np.clip(strength, 0, 1.5)),
                            tileGridSize=(10, 10))
    local = clahe.apply(stretched)

    # 5) Scanner-like crispness: unsharp mask, intentionally mild.
    blur = cv2.GaussianBlur(local, (0, 0), 0.9)
    amount = 0.22 * float(np.clip(strength, 0.0, 1.5))
    sharp = cv2.addWeighted(local, 1.0 + amount, blur, -amount, 0)

    # 6) Soft background lift. Avoid clipping the dark foreground.
    #    This makes the page look whiter without erasing thin lines.
    page_floor = np.percentile(sharp, 30)
    if page_floor > 90:
        lift = min(12.0, (page_floor - 90.0) * 0.35)
        sharp = np.clip(sharp.astype(np.float32) + lift, 0, 255).astype(np.uint8)

    return sharp


def standardize_document_image(image_bgr, target_bg=245):
    """Scanner-style enhancement tuned for OMR.

    Public scanner apps such as CamScanner expose auto-crop, perspective
    correction, background removal and multiple enhance/filter modes. Their
    exact proprietary internals are not public, so this function recreates the
    observable document-scan behavior with OpenCV rather than claiming to
    reproduce CamScanner's private algorithm.
    """
    if image_bgr is None or image_bgr.size == 0:
        return image_bgr

    # OMR reads luminance. Work mainly in grayscale, then return a 3-channel
    # image for compatibility with the existing detector pipeline.
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr.copy()
    clean = enhance_scan_gray(gray, strength=1.0)

    # Keep the requested background target as a gentle white-point adjustment,
    # not a hard threshold. This preserves pencil/pen strokes and fine borders.
    if target_bg != 245:
        mean_bg = float(np.percentile(clean, 70))
        if mean_bg > 1:
            gain = float(target_bg) / mean_bg
            gain = float(np.clip(gain, 0.90, 1.10))
            clean = np.clip(clean.astype(np.float32) * gain, 0, 255).astype(np.uint8)

    return cv2.cvtColor(clean, cv2.COLOR_GRAY2BGR)


def make_scan_detection_image(image, strength=1.0):
    """Fast scanner-like grayscale preprocessing for document/marker detection."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    return enhance_scan_gray(gray, strength=strength)


def prepare_image_for_processing(image, max_width=2200, max_height=2200):
    """Create a processing copy for oversized phone photos.

    This is a resize, not JPEG recompression. The original image is returned
    alongside the resized copy so final perspective warping can still use the
    highest-quality pixels.

    Returns:
        processing_image, scale_x, scale_y
    """
    if image is None or image.size == 0:
        return image, 1.0, 1.0
    h, w = image.shape[:2]
    scale = min(1.0, max_width / float(w), max_height / float(h))
    if scale >= 0.999:
        return image, 1.0, 1.0
    out = cv2.resize(image, (max(1, int(round(w * scale))),
                             max(1, int(round(h * scale)))),
                     interpolation=cv2.INTER_AREA)
    return out, scale, scale


def detect_corners_and_crop(
    image,
    canvas_w=1700,
    canvas_h=2400,
    preferred_method="aruco",
    expected_ids=None,
    dict_name=None,
    crop_mode="inner",
    apply_standardization=True,
    scan_enhance=True
):
    """Fast, robust alignment pipeline.

    Fast path:
        original -> one low-resolution scanner-like preprocessing pass ->
        document/marker detection on the preprocessed image -> rough warp ->
        final perspective warp from the original.

    If ArUco is unavailable (which is common for this LJK's registration
    marks), the cheap RegMark detector is used before falling back to the
    physical page contour. No Google Drive/background step is needed here.
    """
    if image is None or image.size == 0:
        return None, None, "none", None, None, "FAILED: gambar kosong"

    image_bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim != 3 else image
    h_in, w_in = image_bgr.shape[:2]

    # One detection copy only. Original pixels remain untouched for final warp.
    processing_img, sx, sy = prepare_image_for_processing(
        image_bgr, max_width=1800, max_height=1800
    )

    # IMPORTANT: build the scanner-like detection image BEFORE any corner
    # detection.  This is intentionally different from the old pipeline,
    # where the paper boundary was found on the raw photo first.  Flattening
    # illumination + contrast normalization makes the sheet edge and the
    # corner registration marks much more consistent under shadows, glare and
    # uneven exposure.
    preprocessed_gray = make_scan_detection_image(processing_img, strength=1.0)
    preprocessed_bgr = cv2.cvtColor(preprocessed_gray, cv2.COLOR_GRAY2BGR)

    # Do not brute-force rotations. The page detector and marker detector are
    # rotation tolerant. 180 degrees is only a cheap fallback if the first
    # pass genuinely fails. 90/270 are reserved for the rare orientation case.
    candidate_angles = [0, 180, 90, 270]
    best_doc = None

    for angle_index, ang in enumerate(candidate_angles):
        # After a real marker-based result, stop immediately.
        if best_doc is not None and best_doc[1] in ("aruco", "regmark"):
            break
        # Do not pay for 90/270 unless the first two orientations produced no
        # usable anchor at all.
        if angle_index >= 2 and best_doc is not None:
            break

        rot_img = _rotate_candidate(preprocessed_bgr, ang)

        # Corner detection happens on the scanner-like PREPROCESSED image.
        doc_corners, _ = find_document_corners(
            rot_img, target_w=canvas_w, target_h=canvas_h
        )
        doc_corners = order_points(doc_corners, target_w=canvas_w, target_h=canvas_h)

        # Cheap rough warp only for marker detection. Keep this below the final
        # OMR canvas size because it is disposable detection data.
        rough_w = min(1800, max(1400, canvas_w))
        rough_h = min(2500, max(2000, canvas_h))
        rough_img, _, inv_rough_M = _rough_warp(rot_img, doc_corners, rough_w, rough_h)

        # The rough image is already scanner-preprocessed, so do NOT run the
        # expensive enhancement a second time before ArUco/RegMark detection.
        # A simple grayscale conversion is enough here.
        rough_scan_bgr = rough_img

        # 1) ArUco fast path, restricted to four corner ROIs.
        if preferred_method in ("aruco", "auto"):
            ar_pts, ids, detected_dict, ar_status = find_aruco_markers(
                rough_scan_bgr,
                dict_name=dict_name,
                expected_ids=expected_ids,
                crop_mode="inner",  # Always inner for the main alignment path.
            )
            if ar_pts is not None and ar_status == "DETECTED":
                fine_rot = _map_points_back(ar_pts, inv_rough_M)
                if fine_rot is not None:
                    fine_proc = fine_rot
                    fine_original = fine_proc / np.array([sx, sy], dtype=np.float32)
                    if ang:
                        fine_original = np.array(
                            [unrotate_point(p, image_bgr.shape, ang) for p in fine_original],
                            dtype=np.float32,
                        )
                    if _quad_quality(fine_original, image_bgr.shape) >= 0.20:
                        best_doc = (
                            fine_original, "aruco", ids, detected_dict,
                            "DETECTED (4 Sudut Terkunci - ArUco INNER)"
                        )
                        break

        # 2) RegMark path. This is the actual fast anchor mechanism for the
        # supplied LJK image and uses INNER points by default.
        if preferred_method in ("aruco", "auto", "regmark"):
            pts_reg, status_reg = find_regmarks(
                rough_scan_bgr,
                target_w=canvas_w,
                target_h=canvas_h,
                crop_mode="inner",
                doc_corners=np.array([[0, 0], [rough_w - 1, 0], [rough_w - 1, rough_h - 1], [0, rough_h - 1]], dtype=np.float32),
            )
            if pts_reg is not None and status_reg == "DETECTED":
                fine_rot_reg = _map_points_back(pts_reg, inv_rough_M)
                if fine_rot_reg is None:
                    fine_rot_reg = pts_reg
                pts_original = fine_rot_reg / np.array([sx, sy], dtype=np.float32)
                if ang:
                    pts_original = np.array(
                        [unrotate_point(p, image_bgr.shape, ang) for p in pts_original],
                        dtype=np.float32,
                    )
                if _quad_quality(pts_original, image_bgr.shape) >= 0.20:
                    best_doc = (
                        pts_original, "regmark", None, None,
                        "DETECTED (4 Sudut Terkunci - RegMark INNER)"
                    )
                    break

        # 3) Physical page contour is only a last-resort fallback. It is OUTER
        # by definition, so make that explicit in the returned status.
        doc_original = doc_corners / np.array([sx, sy], dtype=np.float32)
        if ang:
            doc_original = np.array(
                [unrotate_point(p, image_bgr.shape, ang) for p in doc_original],
                dtype=np.float32,
            )
        score = _quad_quality(doc_original, image_bgr.shape)
        if best_doc is None or score > _quad_quality(best_doc[0], image_bgr.shape):
            best_doc = (
                doc_original, "doc_contour", None, None,
                "DETECTED (Fallback - Batas Fisik Dokumen OUTER)"
            )

    if best_doc is None:
        return None, None, "none", None, None, "FAILED: corner tidak ditemukan"

    ordered_pts, method_used, corner_ids, detected_dict, status = best_doc

    # Final warp from original image. Linear interpolation is substantially
    # cheaper than cubic and is sufficient for the canonical OMR canvas.
    warped_img, _ = perspective_warp(
        image_bgr, ordered_pts, canvas_w, canvas_h, interpolation=cv2.INTER_LINEAR
    )
    if apply_standardization and scan_enhance and warped_img is not None and warped_img.size > 0:
        warped_img = standardize_document_image(warped_img)
    elif apply_standardization and warped_img is not None and warped_img.size > 0:
        warped_img = standardize_document_image(warped_img, target_bg=255)

    return warped_img, ordered_pts, method_used, corner_ids, detected_dict, status


def perspective_warp(image, src_points, dst_width, dst_height, interpolation=cv2.INTER_LINEAR):
    src = order_points(src_points, target_w=dst_width, target_h=dst_height)
    dst = np.array([
        [0, 0], [dst_width - 1, 0],
        [dst_width - 1, dst_height - 1], [0, dst_height - 1]
    ], dtype=np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(
        image, M, (dst_width, dst_height),
        flags=interpolation,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return warped, M


def draw_regmarks_overlay(image, ordered_pts, method="aruco", corner_ids=None,
                          status="DETECTED", crop_mode="inner"):
    output = image.copy()
    if ordered_pts is None:
        return output

    labels = ["TL", "TR", "BR", "BL"]
    pts_int = np.asarray(ordered_pts, dtype=np.int32)
    cv2.polylines(output, [pts_int], True, (0, 230, 0), 3, cv2.LINE_AA)

    for i, (label, p) in enumerate(zip(labels, ordered_pts)):
        cx, cy = int(round(p[0])), int(round(p[1]))
        cv2.circle(output, (cx, cy), 18, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.circle(output, (cx, cy), 6, (0, 255, 0), -1, cv2.LINE_AA)
        cv2.line(output, (cx - 24, cy), (cx + 24, cy), (0, 0, 255), 2, cv2.LINE_AA)
        cv2.line(output, (cx, cy - 24), (cx, cy + 24), (0, 0, 255), 2, cv2.LINE_AA)

        id_str = f" [ID:{corner_ids[label]}]" if corner_ids and label in corner_ids else ""
        text = f" {label}{id_str} ({cx}, {cy}) "
        text_y = cy - 20 if i in (0, 1) else cy + 34
        text_x = max(10, cx - 65)
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)
        cv2.rectangle(output, (text_x - 3, text_y - th - 3),
                      (text_x + tw + 3, text_y + baseline + 2), (0, 0, 0), -1)
        cv2.putText(output, text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.58, (0, 255, 255), 2, cv2.LINE_AA)

    return output


def rotate_image(image, angle):
    return _rotate_candidate(image, angle)
