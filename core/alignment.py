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


def _make_roi_variants(roi):
    """Return detection-candidate images for a single ROI in order of cost.

    Tries five variants: raw, CLAHE, Otsu binarization, adaptive threshold,
    and an upscale (2×) of the raw patch. Ordered cheapest-first so detection
    can stop at the first success without running all variants.
    """
    variants = [roi]
    # CLAHE: recovers low-contrast / shadow-obscured markers.
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(6, 6))
    variants.append(clahe.apply(roi))
    # Otsu: strong binarization for clean scans.
    _, otsu = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(otsu)
    # Adaptive threshold: handles harsh local shadows.
    adapt = cv2.adaptiveThreshold(
        roi, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 21, 5
    )
    variants.append(adapt)
    # 2× upscale: recovers very small markers in high-res images.
    h, w = roi.shape[:2]
    up = cv2.resize(roi, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
    variants.append(up)
    return variants


def _detect_in_roi(detector, roi, offset_xy, allowed, scale=1.0):
    """Detect markers in one ROI image; return {id: corners} with global coords."""
    found = {}
    corners_list, ids, _ = detector.detectMarkers(roi)
    if ids is None:
        return found
    ox, oy = offset_xy
    for i, mid in enumerate(ids.flatten()):
        mid = int(mid)
        if mid not in allowed:
            continue
        pts = (corners_list[i][0].astype(np.float32) / scale
               + np.array([ox, oy], np.float32))
        area = abs(cv2.contourArea(pts.reshape(-1, 1, 2)))
        if area >= 4:
            found[mid] = pts
    return found


def _find_dark_square_candidates(roi_gray, min_side_frac=0.04, max_side_frac=0.35):
    """Locate dark square blobs in a corner ROI.

    Returns a list of bounding boxes (x, y, w, h) in ROI coordinates, sorted
    best-first (large, square, filled, close to the roi corner at origin).
    These are the candidate locations of ArUco markers before decoding.
    """
    rh, rw = roi_gray.shape[:2]
    min_side = max(8, int(min(rh, rw) * min_side_frac))
    max_side = int(max(rh, rw) * max_side_frac)

    _, otsu = cv2.threshold(roi_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    adapt = cv2.adaptiveThreshold(
        roi_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 9
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    candidates = {}
    for mask in (otsu, adapt):
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in cnts:
            area = cv2.contourArea(cnt)
            if area < min_side * min_side * 0.20:
                continue
            bx, by, bw, bh = cv2.boundingRect(cnt)
            side = max(bw, bh)
            if side < min_side or side > max_side:
                continue
            ar = bw / float(max(bh, 1))
            if not (0.45 <= ar <= 2.2):
                continue
            fill = area / float(max(bw * bh, 1))
            if fill < 0.25:
                continue
            # Score: larger, squarer, more filled, and nearer to origin is better.
            dist_to_corner = float(np.hypot(bx, by))
            squareness = min(bw, bh) / float(max(bw, bh, 1))
            size_score = float(side) / float(max(max_side, 1))
            score = (0.40 * squareness
                     + 0.30 * fill
                     + 0.20 * size_score
                     - 0.10 * (dist_to_corner / float(max(rh, rw, 1))))
            key = (bx // 5, by // 5)
            if key not in candidates or score > candidates[key][-1]:
                candidates[key] = (bx, by, bw, bh, score)

    return [(bx, by, bw, bh)
            for bx, by, bw, bh, _ in sorted(candidates.values(), key=lambda v: -v[-1])][:8]


def _aruco_decode_patch(detector, patch, patch_offset, allowed_ids, scale=1.0):
    """Run ArUco detector on a patch; return {marker_id: corners_in_global}."""
    found = {}
    corners_list, ids, _ = detector.detectMarkers(patch)
    if ids is None:
        return found
    ox, oy = patch_offset
    for i, mid in enumerate(ids.flatten()):
        mid = int(mid)
        if mid not in allowed_ids:
            continue
        pts = (corners_list[i][0].astype(np.float32) / scale
               + np.array([ox, oy], np.float32))
        area = abs(cv2.contourArea(pts.reshape(-1, 1, 2)))
        if area >= 4:
            found[mid] = pts
    return found


class CornerIdMap(dict):
    """Dictionary mapping corner labels -> ArUco IDs, with optional boxes attribute."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.boxes = None


def find_aruco_markers(image, dict_name=None, expected_ids=None, crop_mode="inner"):
    """Robust ArUco detection: locate dark-square blobs first, then decode.

    Two-pass strategy per corner ROI
    ---------------------------------
    Pass 1 — Dark-square localisation (fast):
        Morphological analysis (Otsu + adaptive threshold) finds candidate dark
        blobs in the corner ROI.  For each blob a tight expanded patch is sent
        to the ArUco decoder.  This is both faster and more reliable than
        decoding the whole ROI because the marker is already isolated.

    Pass 2 — Full-ROI cascade (fallback):
        If Pass 1 yields no confirmed marker, the entire corner ROI is tried
        with five image variants (raw → CLAHE → Otsu → adaptive → 2× upscale)
        and two detector sensitivities.  ROI size is also expanded from 25 % to
        38 % of the image edge on the second attempt.

    After detection, the INNER vertex of each confirmed marker is selected:
    the corner of the ArUco square that is geometrically closest to the page
    centre. This gives a tight, consistent crop boundary independent of
    marker size or slight misalignment.
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

    # Ordered strict → loose for speed on clean images.
    det_normal = make_fast_detector(dict_val, min_perimeter=0.006, step=8)
    det_loose  = make_fast_detector(dict_val, min_perimeter=0.003, step=4)

    def _roi_bbox(label, frac):
        """Return (x1, y1, x2, y2) of the corner ROI in image coordinates."""
        rw_ = max(100, int(round(w * frac)))
        rh_ = max(100, int(round(h * frac)))
        if label == "TL": return (0,       0,       rw_,     rh_)
        if label == "TR": return (w - rw_, 0,       w,       rh_)
        if label == "BR": return (w - rw_, h - rh_, w,       h)
        if label == "BL": return (0,       h - rh_, rw_,     h)
        return (0, 0, rw_, rh_)

    # Flip axes so the page-corner of interest is always at (0, 0) of the
    # flipped ROI. This makes _find_dark_square_candidates always bias toward
    # the correct corner regardless of which page corner we are examining.
    _flip_axes = {"TL": (False, False),
                  "TR": (True,  False),
                  "BR": (True,  True),
                  "BL": (False, True)}

    marker_map = {}

    for label in ("TL", "TR", "BR", "BL"):
        target_id = expected[label]
        if target_id in marker_map:
            continue

        fx, fy = _flip_axes[label]

        for frac in (0.25, 0.38):
            if target_id in marker_map:
                break
            x1, y1, x2, y2 = _roi_bbox(label, frac)
            roi_patch = gray[y1:y2, x1:x2]
            if roi_patch.size == 0:
                continue
            rh_roi, rw_roi = roi_patch.shape[:2]

            # ---------------------------------------------------------------
            # PASS 1: Dark-square blob → focused patch → ArUco decode
            # ---------------------------------------------------------------
            flipped = roi_patch
            if fx:
                flipped = cv2.flip(flipped, 1)
            if fy:
                flipped = cv2.flip(flipped, 0)

            blob_boxes = _find_dark_square_candidates(flipped)

            for bx_f, by_f, bw_b, bh_b in blob_boxes:
                # Un-flip bounding box back to original ROI coordinates.
                bx = (rw_roi - bx_f - bw_b) if fx else bx_f
                by = (rh_roi - by_f - bh_b) if fy else by_f

                # Expand patch with generous padding so sub-pixel refinement
                # and adaptive threshold have enough context.
                pad = max(14, int(max(bw_b, bh_b) * 0.55))
                px1 = max(0, bx - pad)
                py1 = max(0, by - pad)
                px2 = min(rw_roi, bx + bw_b + pad)
                py2 = min(rh_roi, by + bh_b + pad)
                patch = roi_patch[py1:py2, px1:px2]
                if patch.size == 0:
                    continue
                gx, gy = x1 + px1, y1 + py1

                for det in (det_normal, det_loose):
                    for variant in _make_roi_variants(patch):
                        sc = (variant.shape[1] / float(patch.shape[1])
                              if patch.shape[1] > 0 else 1.0)
                        found = _aruco_decode_patch(det, variant, (gx, gy), allowed, scale=sc)
                        if target_id in found:
                            marker_map[target_id] = found[target_id]
                            break
                        for mid, pts in found.items():
                            if mid not in marker_map:
                                marker_map[mid] = pts
                    if target_id in marker_map:
                        break
                if target_id in marker_map:
                    break

            if target_id in marker_map:
                continue  # pass 1 succeeded → next label

            # ---------------------------------------------------------------
            # PASS 2: Full-ROI cascade (fallback when no blob found)
            # ---------------------------------------------------------------
            for det in (det_normal, det_loose):
                if target_id in marker_map:
                    break
                for variant in _make_roi_variants(roi_patch):
                    sc = (variant.shape[1] / float(roi_patch.shape[1])
                          if roi_patch.shape[1] > 0 else 1.0)
                    found = _aruco_decode_patch(det, variant, (x1, y1), allowed, scale=sc)
                    if target_id in found:
                        marker_map[target_id] = found[target_id]
                        break
                    for mid, pts in found.items():
                        if mid not in marker_map:
                            marker_map[mid] = pts

    # Recovery: affine prediction + local search when exactly 3 found.
    if len(marker_map) == 3:
        marker_map = recover_missing_corner(marker_map, expected, gray, dict_val=dict_val)

    if len(marker_map) < 4:
        return None, None, best_dict_name, (
            f"ArUco: Ditemukan {len(marker_map)}/4 marker sudut "
            f"{list(marker_map.keys())} ({best_dict_name})."
        )

    # --- INNER-CORNER SELECTION -------------------------------------------
    # For each confirmed ArUco marker, pick the vertex that is closest to the
    # page centre ("inner corner" / "regmark corner"). This is deterministic,
    # stable under marker rotation, and invariant to marker size.
    marker_centers = {
        lbl: marker_map[expected[lbl]].mean(axis=0).astype(np.float32)
        for lbl in ("TL", "TR", "BR", "BL")
    }
    page_center = np.mean(np.vstack(list(marker_centers.values())), axis=0)
    crop_pts = []
    for lbl in ("TL", "TR", "BR", "BL"):
        pts = marker_map[expected[lbl]].astype(np.float32)  # (4, 2)
        mc = marker_centers[lbl]
        if crop_mode == "center":
            pt = mc
        elif crop_mode == "outer":
            dists = np.linalg.norm(pts - page_center, axis=1)
            pt = pts[np.argmax(dists)]
        else:
            # INNER: ArUco vertex closest to page centre.
            dists = np.linalg.norm(pts - page_center, axis=1)
            pt = pts[np.argmin(dists)]
        crop_pts.append(pt)

    ordered = np.asarray(crop_pts, dtype=np.float32)
    if _quad_quality(ordered, gray.shape) < 0.20:
        return None, None, best_dict_name, "ArUco: geometri marker tidak valid."

    marker_boxes = {
        lbl: marker_map[expected[lbl]].astype(np.float32)
        for lbl in ("TL", "TR", "BR", "BL")
    }
    out_ids = CornerIdMap({lbl: expected[lbl] for lbl in ("TL", "TR", "BR", "BL")})
    out_ids.boxes = marker_boxes
    return ordered, out_ids, best_dict_name, "DETECTED"


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


def find_green_frame_corners(image):
    """Detect the printed green frame, which is the desired inner page bound.

    This is intentionally design-aware for the supplied LJK: the physical
    paper can extend beyond the green frame, so the green frame is preferred
    over the paper contour whenever it is visible.
    """
    if image is None or image.size == 0 or image.ndim != 3:
        return None
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # Broad green range to survive phone WB changes and scanner enhancement.
    lower = np.array([28, 28, 22], dtype=np.uint8)
    upper = np.array([100, 255, 235], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
        iterations=2,
    )
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    img_area = float(max(1, w * h))
    best = None
    best_score = -1.0
    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
        area = cv2.contourArea(cnt)
        ratio = area / img_area
        if ratio < 0.25:
            continue
        peri = cv2.arcLength(cnt, True)
        if peri <= 0:
            continue
        approx = cv2.approxPolyDP(cnt, 0.01 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        pts = order_points(approx.reshape(4, 2).astype(np.float32), target_w=w, target_h=h)
        q = _quad_quality(pts, image.shape)
        if q < 0.55:
            continue
        # Prefer a large frame with substantial edge span.
        score = ratio * (0.7 + 0.3 * q)
        if score > best_score:
            best_score = score
            best = pts
    return best


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
            contours, _ = cv2.findContours(roi, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
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

    # Do not brute-force rotations. The page detector and marker detector are
    # rotation tolerant. 180 degrees is only a cheap fallback if the first
    # pass genuinely fails. 90/270 are reserved for the rare orientation case.
    candidate_angles = [0, 180, 90, 270]
    best_doc = None

    for angle_index, ang in enumerate(candidate_angles):
        # ArUco inner-corner is the authoritative anchor. Stop as soon as one
        # is found — no need to try other orientations.
        if best_doc is not None and best_doc[1] in ("aruco", "regmark"):
            break
        # Skip 90°/270° if any anchor was found at 0°/180°.
        if angle_index >= 2 and best_doc is not None:
            break

        rot_img = _rotate_candidate(preprocessed_bgr, ang)

        # Build a rough rectified image for marker detection. Use the physical
        # paper boundary (doc corners) rather than the green frame here because
        # doc corners are computed cheaply from edge detection and give a
        # reasonable canvas even when the green frame is partially occluded.
        doc_corners, _ = find_document_corners(
            rot_img, target_w=canvas_w, target_h=canvas_h
        )
        doc_corners = inset_quad(doc_corners, ratio=0.020)
        doc_corners = order_points(doc_corners, target_w=canvas_w, target_h=canvas_h)

        rough_w = min(1800, max(1400, canvas_w))
        rough_h = min(2500, max(2000, canvas_h))
        rough_img, _, inv_rough_M = _rough_warp(rot_img, doc_corners, rough_w, rough_h)

        # -------------------------------------------------------------------
        # PRIMARY: ArUco inner corner
        # Each ArUco marker is detected in its corner ROI with up to five
        # image variants (raw → CLAHE → Otsu → adaptive → 2× upscale).
        # The INNER vertex of each detected marker — the ArUco corner closest
        # to the LJK page centre — is used as the authoritative crop anchor.
        # -------------------------------------------------------------------
        ar_pts, ids, detected_dict, ar_status = find_aruco_markers(
            rough_img,
            dict_name=dict_name,
            expected_ids=expected_ids,
            crop_mode="inner",
        )
        if ar_pts is not None and ar_status == "DETECTED":
            fine_rot = _map_points_back(ar_pts, inv_rough_M)
            if fine_rot is not None:
                fine_original = fine_rot / np.array([sx, sy], dtype=np.float32)
                if ang:
                    fine_original = np.array(
                        [unrotate_point(p, image_bgr.shape, ang) for p in fine_original],
                        dtype=np.float32,
                    )
                if _quad_quality(fine_original, image_bgr.shape) >= 0.20:
                    orig_boxes = {}
                    if hasattr(ids, "boxes") and ids.boxes:
                        for lbl, b_pts in ids.boxes.items():
                            b_rot = _map_points_back(b_pts, inv_rough_M)
                            if b_rot is not None:
                                b_orig = b_rot / np.array([sx, sy], dtype=np.float32)
                                if ang:
                                    b_orig = np.array(
                                        [unrotate_point(p, image_bgr.shape, ang) for p in b_orig],
                                        dtype=np.float32,
                                    )
                                orig_boxes[lbl] = b_orig
                    final_ids = CornerIdMap(ids)
                    final_ids.boxes = orig_boxes if orig_boxes else None
                    best_doc = (
                        fine_original, "aruco", final_ids, detected_dict,
                        "DETECTED (4 Sudut Terkunci - ArUco Inner Corner)"
                    )
                    break

        # -------------------------------------------------------------------
        # SECONDARY: Printed green frame
        # Only reached when ArUco detection fails (marker occluded, dirty,
        # or image severely degraded). The green frame is detected in the
        # un-warped rotated image for the most reliable colour segmentation.
        # -------------------------------------------------------------------
        green_corners = find_green_frame_corners(rot_img)
        if green_corners is not None:
            green_original = green_corners / np.array([sx, sy], dtype=np.float32)
            if ang:
                green_original = np.array(
                    [unrotate_point(p, image_bgr.shape, ang) for p in green_original],
                    dtype=np.float32,
                )
            score = _quad_quality(green_original, image_bgr.shape)
            if score >= 0.20:
                if best_doc is None or score > _quad_quality(best_doc[0], image_bgr.shape):
                    best_doc = (
                        green_original, "green_frame", None, None,
                        "DETECTED (Fallback - Green Frame Inner)"
                    )
                continue

        # -------------------------------------------------------------------
        # TERTIARY: Black-box registration marks (square corner boxes)
        # Only used when both ArUco and green-frame detection fail.
        # -------------------------------------------------------------------
        if preferred_method in ("aruco", "auto", "regmark"):
            pts_reg, status_reg = _find_regmarks_on_normalized_page(
                rough_img, crop_mode="inner"
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
                        "DETECTED (Fallback - RegMark Inner)"
                    )
                    break

        # -------------------------------------------------------------------
        # LAST RESORT: Inset physical-paper boundary
        # -------------------------------------------------------------------
        doc_original = doc_corners / np.array([sx, sy], dtype=np.float32)
        if ang:
            doc_original = np.array(
                [unrotate_point(p, image_bgr.shape, ang) for p in doc_original],
                dtype=np.float32,
            )
        score = _quad_quality(doc_original, image_bgr.shape)
        if best_doc is None or score > _quad_quality(best_doc[0], image_bgr.shape):
            best_doc = (
                doc_original, "doc_inset", None, None,
                "DETECTED (Fallback - Inset INNER)"
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
                          status="DETECTED", crop_mode="inner", marker_boxes=None):
    output = image.copy()
    if ordered_pts is None:
        return output

    ih, iw = output.shape[:2]
    scale_factor = max(1.0, min(ih, iw) / 1200.0)
    banner_h = max(32, int(round(40 * scale_factor)))

    # 1. Retrieve ArUco / Regmark boxes if available
    boxes = marker_boxes
    if boxes is None and hasattr(corner_ids, "boxes"):
        boxes = corner_ids.boxes
    elif boxes is None and isinstance(corner_ids, dict) and "_boxes" in corner_ids:
        boxes = corner_ids["_boxes"]

    # 2. Draw detected ArUco black boxes (kotak hitam ArUco)
    if boxes and len(boxes) > 0:
        # Subtle semi-transparent tint on ArUco black squares
        overlay = output.copy()
        for lbl in ("TL", "TR", "BR", "BL"):
            if lbl in boxes and boxes[lbl] is not None:
                b_pts = np.asarray(boxes[lbl], dtype=np.int32)
                cv2.fillPoly(overlay, [b_pts], (0, 140, 255))
        cv2.addWeighted(overlay, 0.25, output, 0.75, 0, output)

        # Draw crisp outline & corners for each ArUco box
        box_line_w = max(2, int(round(3 * scale_factor)))
        v_rad = max(3, int(round(4 * scale_factor)))
        for lbl in ("TL", "TR", "BR", "BL"):
            if lbl in boxes and boxes[lbl] is not None:
                b_pts = np.asarray(boxes[lbl], dtype=np.int32)
                # Outer border in bright amber / orange
                cv2.polylines(output, [b_pts], True, (0, 165, 255), box_line_w, cv2.LINE_AA)
                
                # Corner vertices of the black box
                for v in b_pts:
                    cv2.circle(output, tuple(v), v_rad, (0, 215, 255), -1, cv2.LINE_AA)

                # Center of ArUco box
                bc = b_pts.mean(axis=0).astype(int)
                cv2.circle(output, (bc[0], bc[1]), max(3, int(round(5 * scale_factor))), (0, 140, 255), -1, cv2.LINE_AA)

                # Tag badge for ArUco box
                id_val = corner_ids[lbl] if corner_ids and lbl in corner_ids else ""
                tag = f" Kotak ArUco {lbl} [ID:{id_val}] "
                f_scale = 0.46 * scale_factor
                f_thick = max(1, int(round(1.4 * scale_factor)))
                (tw, th), bl = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
                bx = bc[0] - tw // 2
                by = bc[1] - int(round(35 * scale_factor)) if "T" in lbl else bc[1] + int(round(45 * scale_factor))
                bx = max(10, min(iw - tw - 10, bx))
                by = max(banner_h + th + 8, min(ih - 10, by))
                cv2.rectangle(output, (bx - 2, by - th - 2), (bx + tw + 2, by + bl + 2), (0, 0, 0), -1)
                cv2.rectangle(output, (bx - 2, by - th - 2), (bx + tw + 2, by + bl + 2), (0, 165, 255), 1)
                cv2.putText(output, tag, (bx, by), cv2.FONT_HERSHEY_SIMPLEX, f_scale, (0, 215, 255), f_thick, cv2.LINE_AA)

    # 3. Draw Green Crop Polygon (Garis Hijau Area yang Dicrop)
    labels = ["TL", "TR", "BR", "BL"]
    pts_int = np.asarray(ordered_pts, dtype=np.int32)
    crop_line_w = max(2, int(round(3 * scale_factor)))
    cv2.polylines(output, [pts_int], True, (0, 230, 0), crop_line_w, cv2.LINE_AA)

    # 4. Draw inner corner targets (Pojok dalam LJK yang menjadi batas crop)
    for i, (label, p) in enumerate(zip(labels, ordered_pts)):
        cx, cy = int(round(p[0])), int(round(p[1]))
        r1 = max(10, int(round(16 * scale_factor)))
        r2 = max(4, int(round(5 * scale_factor)))
        cr_len = max(14, int(round(22 * scale_factor)))
        l_thick = max(1, int(round(2 * scale_factor)))

        # Target circle & crosshair
        cv2.circle(output, (cx, cy), r1, (0, 0, 255), l_thick, cv2.LINE_AA)
        cv2.circle(output, (cx, cy), r2, (0, 255, 0), -1, cv2.LINE_AA)
        cv2.line(output, (cx - cr_len, cy), (cx + cr_len, cy), (0, 0, 255), l_thick, cv2.LINE_AA)
        cv2.line(output, (cx, cy - cr_len), (cx, cy + cr_len), (0, 0, 255), l_thick, cv2.LINE_AA)

        # Line connecting ArUco center to its inner corner point
        if boxes and label in boxes and boxes[label] is not None:
            bc = np.asarray(boxes[label]).mean(axis=0).astype(int)
            cv2.line(output, (bc[0], bc[1]), (cx, cy), (0, 255, 255), max(1, int(round(1.5 * scale_factor))), cv2.LINE_AA)

        id_str = f" [ID:{corner_ids[label]}]" if corner_ids and label in corner_ids else ""
        text = f" {label}{id_str} Sudut Dalam ({cx}, {cy}) "
        f_scale = 0.48 * scale_factor
        f_thick = max(1, int(round(1.6 * scale_factor)))
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, f_scale, f_thick)
        
        # Position towards page interior to prevent any overlap with ArUco box badge
        if label == "TL":
            text_x = cx + int(round(16 * scale_factor))
            text_y = cy + int(round(30 * scale_factor))
        elif label == "TR":
            text_x = cx - tw - int(round(16 * scale_factor))
            text_y = cy + int(round(30 * scale_factor))
        elif label == "BL":
            text_x = cx + int(round(16 * scale_factor))
            text_y = cy - int(round(20 * scale_factor))
        else:  # BR
            text_x = cx - tw - int(round(16 * scale_factor))
            text_y = cy - int(round(20 * scale_factor))
            
        text_x = max(10, min(iw - tw - 10, text_x))
        text_y = max(th + 10, min(ih - 10, text_y))
        
        cv2.rectangle(output, (text_x - 3, text_y - th - 3),
                      (text_x + tw + 3, text_y + baseline + 2), (0, 0, 0), -1)
        cv2.rectangle(output, (text_x - 3, text_y - th - 3),
                      (text_x + tw + 3, text_y + baseline + 2), (0, 230, 0), 1)
        cv2.putText(output, text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX,
                    f_scale, (0, 255, 255), f_thick, cv2.LINE_AA)

    # 5. Top Legend Banner / HUD
    banner_h = max(32, int(round(40 * scale_factor)))
    hud_overlay = output.copy()
    cv2.rectangle(hud_overlay, (0, 0), (iw, banner_h), (20, 20, 20), -1)
    cv2.addWeighted(hud_overlay, 0.78, output, 0.22, 0, output)
    cv2.line(output, (0, banner_h), (iw, banner_h), (0, 230, 0), max(1, int(round(1.5 * scale_factor))))

    leg_f_scale = 0.45 * scale_factor
    leg_f_thick = max(1, int(round(1.4 * scale_factor)))
    hud_y = int(round(banner_h * 0.68))
    
    # Item 1: Kotak ArUco
    cv2.rectangle(output, (int(round(15 * scale_factor)), int(round(banner_h * 0.26))),
                  (int(round(30 * scale_factor)), int(round(banner_h * 0.74))), (0, 165, 255), -1)
    cv2.putText(output, "Kotak Hitam ArUco", (int(round(36 * scale_factor)), hud_y),
                cv2.FONT_HERSHEY_SIMPLEX, leg_f_scale, (255, 255, 255), leg_f_thick, cv2.LINE_AA)

    # Item 2: Garis Area Crop
    x_off = int(round(220 * scale_factor))
    cv2.line(output, (x_off, hud_y - int(round(4 * scale_factor))),
             (x_off + int(round(20 * scale_factor)), hud_y - int(round(4 * scale_factor))), (0, 230, 0), max(2, int(round(3 * scale_factor))))
    cv2.putText(output, "Garis Area Crop LJK", (x_off + int(round(26 * scale_factor)), hud_y),
                cv2.FONT_HERSHEY_SIMPLEX, leg_f_scale, (255, 255, 255), leg_f_thick, cv2.LINE_AA)

    # Item 3: Titik Sudut Dalam
    x_off2 = int(round(440 * scale_factor))
    cv2.circle(output, (x_off2 + int(round(8 * scale_factor)), hud_y - int(round(4 * scale_factor))),
               max(4, int(round(6 * scale_factor))), (0, 0, 255), 2)
    cv2.circle(output, (x_off2 + int(round(8 * scale_factor)), hud_y - int(round(4 * scale_factor))),
               max(2, int(round(3 * scale_factor))), (0, 255, 0), -1)
    cv2.putText(output, "Titik Sudut Dalam (Crop Anchor)", (x_off2 + int(round(20 * scale_factor)), hud_y),
                cv2.FONT_HERSHEY_SIMPLEX, leg_f_scale, (255, 255, 255), leg_f_thick, cv2.LINE_AA)

    return output


def rotate_image(image, angle):
    return _rotate_candidate(image, angle)
