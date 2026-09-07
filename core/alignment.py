"""Robust document alignment and ArUco corner detection.

Pipeline:
    1. Create one bounded detection-resolution copy.
    2. Detect the physical paper once.
    3. Rough-warp only for corner-anchor detection.
    4. Detect ArUco only in the four corner ROIs.
    5. Use RegMark INNER corners when the sheet uses registration marks.
    6. Fall back to the printed green frame, then an inward physical-paper estimate.
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
# Printed inner frame detection
# ---------------------------------------------------------------------------

def enhance_scan_bgr(image, strength=1.0):
    """Scanner-like enhancement while preserving color information.

    Used before geometric corner detection so the green printed frame remains
    available as a strong, design-specific boundary cue.
    """
    if image is None or image.size == 0:
        return image
    bgr = image.copy()
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = enhance_scan_gray(l, strength=strength)
    clahe = cv2.createCLAHE(clipLimit=1.4, tileGridSize=(10, 10))
    l = clahe.apply(l)
    out = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
    return out



def _fit_inner_green_edge(points, orientation, image_shape):
    """Fit one straight inner-edge line from green pixels.

    The key idea is to detect the *inside boundary of the green band*, not the
    outside contour of the green mask. This prevents the crop from jumping
    between the outer and inner edge of the printed frame.
    """
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if len(pts) < 30:
        return None
    h, w = image_shape[:2]
    # Remove extreme outliers with a robust median/MAD filter on the varying
    # coordinate. This keeps text/green blocks from pulling the fitted edge.
    if orientation in ("top", "bottom"):
        coord = pts[:, 1]
    else:
        coord = pts[:, 0]
    med = float(np.median(coord))
    mad = float(np.median(np.abs(coord - med))) + 1.0
    keep = np.abs(coord - med) <= max(12.0, 4.5 * mad)
    pts = pts[keep]
    if len(pts) < 20:
        return None

    # Subsample for stable and cheap line fitting.
    if len(pts) > 2500:
        idx = np.linspace(0, len(pts) - 1, 2500).astype(np.int32)
        pts = pts[idx]

    # Fit y = ax + b for horizontal edges or x = ay + b for vertical edges.
    if orientation in ("top", "bottom"):
        x = pts[:, 0]
        y = pts[:, 1]
        A = np.column_stack([x, np.ones_like(x)])
        a, b = np.linalg.lstsq(A, y, rcond=None)[0]
        x0, x1 = 0.0, float(w - 1)
        return np.array([[x0, a * x0 + b], [x1, a * x1 + b]], np.float32)
    else:
        y = pts[:, 1]
        x = pts[:, 0]
        A = np.column_stack([y, np.ones_like(y)])
        a, b = np.linalg.lstsq(A, x, rcond=None)[0]
        y0, y1 = 0.0, float(h - 1)
        return np.array([[a * y0 + b, y0], [a * y1 + b, y1]], np.float32)


def _intersect_lines(p1, p2, q1, q2):
    """2-D infinite line intersection; None for near-parallel lines."""
    p1 = np.asarray(p1, dtype=np.float64)
    p2 = np.asarray(p2, dtype=np.float64)
    q1 = np.asarray(q1, dtype=np.float64)
    q2 = np.asarray(q2, dtype=np.float64)
    r = p2 - p1
    s = q2 - q1
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-8:
        return None
    t = ((q1[0] - p1[0]) * s[1] - (q1[1] - p1[1]) * s[0]) / den
    out = p1 + t * r
    return out.astype(np.float32)



def _first_green_run(values, from_start=True, min_run=3, max_gap=1):
    """Return the first reasonably sized contiguous green run along a scanline."""
    idx = np.flatnonzero(values > 0)
    if idx.size == 0:
        return None
    if from_start:
        ordered = idx
    else:
        ordered = idx[::-1]
    run_start = int(ordered[0])
    prev = int(ordered[0])
    for raw in ordered[1:]:
        cur = int(raw)
        if abs(cur - prev) > max_gap + 1:
            lo, hi = min(run_start, prev), max(run_start, prev)
            if hi - lo + 1 >= min_run:
                return lo, hi
            run_start = cur
        prev = cur
    lo, hi = min(run_start, prev), max(run_start, prev)
    if hi - lo + 1 >= min_run:
        return lo, hi
    return None


def find_green_frame_inner_corners(image):
    """Find the INNER boundary of the outer printed green frame.

    The old implementation selected the outer contour of the entire green
    segmentation. That is unstable because the sheet contains many other green
    regions. Here we treat the frame as a border band: for each scan line we
    take the *first green run encountered from the physical page edge* and use
    its edge facing the page interior. This directly represents the inner edge
    of the green frame and is much harder to confuse with inner green boxes.
    """
    if image is None or image.size == 0 or image.ndim != 3:
        return None
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower = np.array([28, 35, 18], dtype=np.uint8)
    upper = np.array([100, 255, 245], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        iterations=1,
    )

    # Use central scan lines only. Corners contain registration marks and can
    # locally disrupt the green border.
    edge_frac = 0.08
    x_lo, x_hi = int(w * edge_frac), int(w * (1.0 - edge_frac))
    y_lo, y_hi = int(h * edge_frac), int(h * (1.0 - edge_frac))
    top_limit = int(h * 0.28)
    bottom_limit = int(h * 0.72)
    left_limit = int(w * 0.28)
    right_limit = int(w * 0.72)

    top_pts, bottom_pts, left_pts, right_pts = [], [], [], []

    for x in range(x_lo, x_hi + 1, 3):
        run = _first_green_run(mask[:top_limit, x], from_start=True)
        if run is not None:
            _, end = run
            top_pts.append((x, end))  # inner-facing edge of top green band

    for x in range(x_lo, x_hi + 1, 3):
        col = mask[bottom_limit:h, x]
        run = _first_green_run(col, from_start=False)
        if run is not None:
            start, _ = run
            bottom_pts.append((x, bottom_limit + start))  # inner-facing edge

    for y in range(y_lo, y_hi + 1, 3):
        run = _first_green_run(mask[y, :left_limit], from_start=True)
        if run is not None:
            _, end = run
            left_pts.append((end, y))

    for y in range(y_lo, y_hi + 1, 3):
        row = mask[y, right_limit:w]
        run = _first_green_run(row, from_start=False)
        if run is not None:
            start, _ = run
            right_pts.append((right_limit + start, y))

    edge_points = {
        "top": np.asarray(top_pts, np.float32),
        "bottom": np.asarray(bottom_pts, np.float32),
        "left": np.asarray(left_pts, np.float32),
        "right": np.asarray(right_pts, np.float32),
    }

    lines = {}
    for name in ("top", "bottom", "left", "right"):
        lines[name] = _fit_inner_green_edge(edge_points[name], name, image.shape)

    if any(v is None for v in lines.values()):
        return None

    tl = _intersect_lines(lines["top"][0], lines["top"][1], lines["left"][0], lines["left"][1])
    tr = _intersect_lines(lines["top"][0], lines["top"][1], lines["right"][0], lines["right"][1])
    br = _intersect_lines(lines["bottom"][0], lines["bottom"][1], lines["right"][0], lines["right"][1])
    bl = _intersect_lines(lines["bottom"][0], lines["bottom"][1], lines["left"][0], lines["left"][1])
    if any(v is None for v in (tl, tr, br, bl)):
        return None

    pts = np.asarray([tl, tr, br, bl], dtype=np.float32)
    pts = np.clip(pts, [0, 0], [w - 1, h - 1]).astype(np.float32)
    if _quad_quality(pts, image.shape) < 0.40:
        return None
    return pts


def find_green_frame_corners(image):
    """Compatibility wrapper: return the INNER green frame corners."""
    return find_green_frame_inner_corners(image)

def inset_quad(points, ratio=0.028):
    """Move a fallback document quad inward so fallback remains INNER semantics."""
    pts = order_points(points)
    c = pts.mean(axis=0)
    out = c + (pts - c) * (1.0 - float(np.clip(ratio, 0.0, 0.15)))
    return out.astype(np.float32)


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
                if q < 0.45:
                    continue
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

def _find_regmarks_on_normalized_page(image, crop_mode="inner"):
    """Detect four square registration marks on an already rectified page.

    The detector is conservative because false positives near the page corners
    are much worse than a missed mark. Candidate size is constrained relative
    to the page, candidates must be close to their expected corner, and the
    returned point is the geometric INNER corner of a fitted square/box.
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    page_center = np.array([w / 2.0, h / 2.0], dtype=np.float32)
    min_dim = float(min(w, h))

    # Scan-like preprocessing may already have boosted contrast. Keep two
    # masks only: Otsu is fast; adaptive threshold handles local shadows.
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    adapt = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        31, 7
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    masks = [
        cv2.morphologyEx(otsu, cv2.MORPH_OPEN, kernel, iterations=1),
        cv2.morphologyEx(adapt, cv2.MORPH_OPEN, kernel, iterations=1),
    ]

    corner_defs = {
        "TL": np.array([0.0, 0.0], dtype=np.float32),
        "TR": np.array([w - 1.0, 0.0], dtype=np.float32),
        "BR": np.array([w - 1.0, h - 1.0], dtype=np.float32),
        "BL": np.array([0.0, h - 1.0], dtype=np.float32),
    }

    points = []
    found = 0
    # Registration marks on this LJK are small relative to the page. These
    # bounds deliberately reject large text boxes and OMR blocks.
    min_side = max(10.0, min_dim * 0.012)
    max_side = min_dim * 0.075
    max_corner_distance = min_dim * 0.18

    for label, corner in corner_defs.items():
        direction = page_center - corner
        norm = float(np.linalg.norm(direction))
        unit = direction / max(norm, 1e-6)
        # Search only a wedge close to the physical corner.
        depth_x = abs(direction[0]) * 0.24
        depth_y = abs(direction[1]) * 0.24
        x1, x2 = sorted((int(corner[0]), int(np.clip(corner[0] + np.sign(direction[0]) * depth_x, 0, w - 1))))
        y1, y2 = sorted((int(corner[1]), int(np.clip(corner[1] + np.sign(direction[1]) * depth_y, 0, h - 1))))

        best = None
        best_score = -1.0
        for mask in masks:
            roi = mask[y1:y2 + 1, x1:x2 + 1]
            contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < min_side * min_side * 0.18:
                    continue
                bx, by, bw, bh = cv2.boundingRect(cnt)
                side = max(bw, bh)
                if side < min_side or side > max_side:
                    continue
                ar = bw / float(max(bh, 1))
                if not (0.70 <= ar <= 1.43):
                    continue
                rect_area = float(bw * bh)
                fill = area / max(rect_area, 1.0)
                if fill < 0.48:
                    continue

                shifted = cnt.reshape(-1, 2).astype(np.float32) + np.array([x1, y1], np.float32)
                c = shifted.mean(axis=0)
                dist = float(np.linalg.norm(c - corner))
                if dist > max_corner_distance:
                    continue

                # Fit the candidate with a rotated rectangle. For a printed
                # registration mark the fitted box is more stable than an
                # arbitrary contour vertex.
                rect = cv2.minAreaRect(shifted.reshape(-1, 1, 2))
                (rcx, rcy), (rw_box, rh_box), _ = rect
                if rw_box <= 1 or rh_box <= 1:
                    continue
                box_side = max(rw_box, rh_box)
                if box_side < min_side or box_side > max_side:
                    continue
                square = min(rw_box, rh_box) / max(rw_box, rh_box)
                corner_dist_score = 1.0 - min(dist / max_corner_distance, 1.0)
                size_score = 1.0 - min(abs(box_side - (min_side + max_side) * 0.35) /
                                       max((max_side - min_side) * 0.7, 1.0), 1.0)
                score = 0.45 * corner_dist_score + 0.30 * square + 0.15 * fill + 0.10 * size_score
                if score > best_score:
                    best_score = score
                    best = np.array(cv2.boxPoints(rect), dtype=np.float32)

        if best is None:
            points.append(corner.copy())
            continue

        found += 1
        center = best.mean(axis=0)
        if crop_mode == "center":
            pt = center
        else:
            # Pick the box vertex whose direction is most aligned with the
            # vector from the marker toward the page center.
            projs = (best - center) @ unit
            if crop_mode == "outer":
                pt = best[np.argmin(projs)]
            else:
                pt = best[np.argmax(projs)]
        points.append(pt.astype(np.float32))

    if found == 4:
        pts = order_points(np.asarray(points, dtype=np.float32), target_w=w, target_h=h)
        if _quad_quality(pts, gray.shape) >= 0.35:
            return pts, "DETECTED"
    return None, f"RegMark: Ditemukan {found}/4 marker."


def find_regmarks(image, target_w=1700, target_h=2400, crop_mode="inner", doc_corners=None):
    """Detect registration marks; public wrapper retained for compatibility."""
    h, w = image.shape[:2]
    if doc_corners is None:
        doc_corners, _ = find_document_corners(image, target_w=target_w, target_h=target_h)
        doc_corners = order_points(doc_corners, target_w=target_w, target_h=target_h)
    rough, _, _ = _rough_warp(image, doc_corners, 1200, 1700)
    pts, status = _find_regmarks_on_normalized_page(rough, crop_mode=crop_mode)
    if pts is None:
        return doc_corners, "FALLBACK_DOC_CORNERS"
    src = np.array([[0, 0], [rough.shape[1] - 1, 0],
                    [rough.shape[1] - 1, rough.shape[0] - 1], [0, rough.shape[0] - 1]], np.float32)
    H = cv2.getPerspectiveTransform(src, doc_corners.astype(np.float32))
    mapped = _map_points_back(pts, H)
    return order_points(mapped, target_w=target_w, target_h=target_h), status


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
    marks), the RegMark detector is used, then the printed green frame is
    preferred. All successful paths preserve INNER-crop semantics.
    """
    if image is None or image.size == 0:
        return None, None, "none", None, None, "FAILED: gambar kosong"

    image_bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim != 3 else image
    h_in, w_in = image_bgr.shape[:2]

    # One detection copy only. Original pixels remain untouched for final warp.
    processing_img, sx, sy = prepare_image_for_processing(
        image_bgr, max_width=2200, max_height=2200
    )

    # IMPORTANT: build the scanner-like detection image BEFORE any corner
    # detection.  This is intentionally different from the old pipeline,
    # where the paper boundary was found on the raw photo first.  Flattening
    # illumination + contrast normalization makes the sheet edge and the
    # corner registration marks much more consistent under shadows, glare and
    # uneven exposure.
    # Preserve color for design-aware frame detection, while also building the
    # enhanced grayscale stream used by ArUco/RegMark detection.
    preprocessed_bgr = enhance_scan_bgr(processing_img, strength=1.0)
    preprocessed_gray = cv2.cvtColor(preprocessed_bgr, cv2.COLOR_BGR2GRAY)

    # Do not brute-force rotations. Start with the input orientation and only
    # spend extra work when the first pass has no reliable geometric anchor.
    candidate_angles = [0, 180, 90, 270]
    best_doc = None

    for angle_index, ang in enumerate(candidate_angles):
        if best_doc is not None and best_doc[1] in ("green_frame", "aruco", "regmark"):
            break
        if angle_index >= 2 and best_doc is not None:
            break

        rot_img = _rotate_candidate(preprocessed_bgr, ang)

        # 1) Find only the physical page at low cost. This is NOT used as the
        # final crop boundary; it only gives us a normalized search canvas.
        physical_corners, _ = find_document_corners(
            rot_img, target_w=canvas_w, target_h=canvas_h
        )
        physical_corners = order_points(
            physical_corners, target_w=canvas_w, target_h=canvas_h
        )

        rough_w = min(1900, max(1500, canvas_w))
        rough_h = min(2500, max(2100, canvas_h))
        rough_img, _, inv_rough_M = _rough_warp(
            rot_img, physical_corners, rough_w, rough_h
        )

        # 2) AUTHORITATIVE CROP BOUNDARY: the INNER edge of the green frame.
        # Detect it after rough rectification, where the frame is close to a
        # rectangle and the four sides can be separated cleanly from internal
        # green boxes. If found, we stop and never replace it with marker
        # corners. This is what guarantees semantic consistency.
        green_pts = find_green_frame_inner_corners(rough_img)
        if green_pts is not None:
            fine_rot = _map_points_back(green_pts, inv_rough_M)
            pts_original = fine_rot / np.array([sx, sy], dtype=np.float32)
            if ang:
                pts_original = np.array(
                    [unrotate_point(p, image_bgr.shape, ang) for p in pts_original],
                    dtype=np.float32,
                )
            if _quad_quality(pts_original, image_bgr.shape) >= 0.20:
                best_doc = (
                    pts_original, "green_frame", None, None,
                    "DETECTED (4 Sudut Terkunci - Green Frame INNER)"
                )
                break

        # 3) Fiducial fallback. The black registration mark itself is not the
        # crop boundary. We use its geometrically INNER vertex only when the
        # green frame cannot be recovered.
        if preferred_method in ("aruco", "auto"):
            ar_pts, ids, detected_dict, ar_status = find_aruco_markers(
                rough_img,
                dict_name=dict_name,
                expected_ids=expected_ids,
                crop_mode="inner",
            )
            if ar_pts is not None and ar_status == "DETECTED":
                fine_rot = _map_points_back(ar_pts, inv_rough_M)
                fine_original = fine_rot / np.array([sx, sy], dtype=np.float32)
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

        if preferred_method in ("aruco", "auto", "regmark"):
            pts_reg, status_reg = _find_regmarks_on_normalized_page(
                rough_img, crop_mode="inner"
            )
            if pts_reg is not None and status_reg == "DETECTED":
                fine_rot_reg = _map_points_back(pts_reg, inv_rough_M)
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

        # 4) Last resort. Keep INNER semantics even when no fiducial survives.
        doc_original = physical_corners / np.array([sx, sy], dtype=np.float32)
        if ang:
            doc_original = np.array(
                [unrotate_point(p, image_bgr.shape, ang) for p in doc_original],
                dtype=np.float32,
            )
        score = _quad_quality(doc_original, image_bgr.shape)
        fallback_status = "DETECTED (Fallback - Inset INNER)"
        if best_doc is None or score > _quad_quality(best_doc[0], image_bgr.shape):
            best_doc = (doc_original, "doc_inset", None, None, fallback_status)

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
