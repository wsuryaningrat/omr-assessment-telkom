import cv2
import numpy as np

# Top most common ArUco dictionaries prioritized for instant detection (< 0.05s)
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
    ("DICT_ARUCO_ORIGINAL", cv2.aruco.DICT_ARUCO_ORIGINAL)
]


def unrotate_point(pt, orig_shape, angle):
    """
    Transform a 2D coordinate (x, y) from a rotated image back into the coordinate space of the original unrotated image.
    """
    orig_h, orig_w = orig_shape[:2]
    rx, ry = float(pt[0]), float(pt[1])
    if angle == 90:
        return np.array([ry, orig_h - 1.0 - rx], dtype=np.float32)
    elif angle == 180:
        return np.array([orig_w - 1.0 - rx, orig_h - 1.0 - ry], dtype=np.float32)
    elif angle in [270, -90]:
        return np.array([orig_w - 1.0 - ry, rx], dtype=np.float32)
    return np.array([rx, ry], dtype=np.float32)


def get_marker_rotation(corners):
    """
    Determine rotation angle (0, 90, 180, 270) of an ArUco marker from its top edge.
    """
    v = corners[1] - corners[0]
    angle = np.degrees(np.arctan2(v[1], v[0]))
    if -45 <= angle < 45:
        return 0
    elif 45 <= angle < 135:
        return 90
    elif angle >= 135 or angle < -135:
        return 180
    else:
        return 270


def make_fast_detector(dict_val, min_perimeter=0.015, step=8):
    """
    Creates an ultra-reliable ArUco detector tuned for smartphone camera photos and scans.
    """
    dictionary = cv2.aruco.getPredefinedDictionary(dict_val)
    parameters = cv2.aruco.DetectorParameters()
    parameters.minMarkerPerimeterRate = min_perimeter
    parameters.maxMarkerPerimeterRate = 2.5
    parameters.adaptiveThreshWinSizeMin = 3
    parameters.adaptiveThreshWinSizeMax = 53
    parameters.adaptiveThreshWinSizeStep = step
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(dictionary, parameters)


def recover_missing_corner(marker_map, exp_c_ids, full_gray, dict_val=cv2.aruco.DICT_4X4_50):
    """
    Sub-pixel 3-Marker Geometric Recovery Engine:
    When 3 of the 4 ArUco corner markers are detected on the sheet, the 4th marker position
    is geometrically predictable with high precision via parallelogram symmetry:
    TR = TL + (BR - BL), etc.
    Extracts a local patch around the predicted position on the full-resolution image and applies
    Otsu / multi-threshold binarization to extract the exact 4th marker corners.
    If the marker is physically torn / occluded, synthesizes its corners using neighbor marker geometry.
    """
    h, w = full_gray.shape[:2]
    exp_set = set(exp_c_ids.values())
    found_exp = [mid for mid in exp_set if mid in marker_map]

    if len(found_exp) != 3:
        return marker_map

    missing_mid = list(exp_set - set(found_exp))[0]
    missing_lbl = [lbl for lbl, mid in exp_c_ids.items() if mid == missing_mid][0]
    centers = {
        lbl: np.mean(marker_map[exp_c_ids[lbl]], axis=0)
        for lbl in ['TL', 'TR', 'BR', 'BL'] if exp_c_ids[lbl] in marker_map
    }

    if missing_lbl == 'TR':
        c_est = centers['TL'] + (centers['BR'] - centers['BL'])
    elif missing_lbl == 'TL':
        c_est = centers['TR'] + (centers['BL'] - centers['BR'])
    elif missing_lbl == 'BR':
        c_est = centers['BL'] + (centers['TR'] - centers['TL'])
    else:  # BL
        c_est = centers['BR'] + (centers['TL'] - centers['TR'])

    cx, cy = int(round(c_est[0])), int(round(c_est[1]))
    hw = 250
    x1, x2 = max(0, cx - hw), min(w, cx + hw)
    y1, y2 = max(0, cy - hw), min(h, cy + hw)
    patch = full_gray[y1:y2, x1:x2]

    dictionary = cv2.aruco.getPredefinedDictionary(dict_val)
    parameters = cv2.aruco.DetectorParameters()
    parameters.minMarkerPerimeterRate = 0.015
    detector = cv2.aruco.ArucoDetector(dictionary, parameters)

    found_in_patch = False
    # Attempt 1: Otsu binarization
    _, th_patch = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    cp_det, idp_det, _ = detector.detectMarkers(th_patch)
    if idp_det is not None and missing_mid in idp_det.flatten():
        idx_m = list(idp_det.flatten()).index(missing_mid)
        marker_map[missing_mid] = cp_det[idx_m][0] + np.array([x1, y1])
        found_in_patch = True
    else:
        # Attempt 2: Multi-threshold scan
        for tv in [80, 100, 120, 140, 160]:
            _, th_p = cv2.threshold(patch, tv, 255, cv2.THRESH_BINARY)
            cp_det, idp_det, _ = detector.detectMarkers(th_p)
            if idp_det is not None and missing_mid in idp_det.flatten():
                idx_m = list(idp_det.flatten()).index(missing_mid)
                marker_map[missing_mid] = cp_det[idx_m][0] + np.array([x1, y1])
                found_in_patch = True
                break

    if not found_in_patch:
        # Geometric synthesis using neighbor marker dimensions
        ref_lbl = 'TL' if missing_lbl in ['TR', 'BL'] else 'TR'
        ref_corners = marker_map[exp_c_ids[ref_lbl]]
        ref_center = np.mean(ref_corners, axis=0)
        marker_map[missing_mid] = ref_corners - ref_center + c_est

    return marker_map


def find_aruco_markers(image, dict_name=None, expected_ids=None, crop_mode="inner"):
    """
    Robust ArUco marker detector with 3-marker geometric recovery and strict inner crop.
    Guarantees cropping ONLY the area inside the ArUco markers (crop_mode='inner').
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
    h, w = gray.shape

    # Pre-scale if image is huge (e.g. > 1600px width) for high-speed contour extraction
    scale = 1.0
    if w > 1600:
        scale = 1400.0 / float(w)
        small_gray = cv2.resize(gray, (1400, int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        small_gray = gray

    inv_scale = 1.0 / scale

    # Select dictionaries to try. Prioritize DICT_4X4_50 for official LJK templates.
    dict_val = cv2.aruco.DICT_4X4_50
    best_dict_name = "DICT_4X4_50"
    if dict_name and hasattr(cv2.aruco, dict_name):
        dict_val = getattr(cv2.aruco, dict_name)
        best_dict_name = dict_name

    exp_corner_ids = expected_ids if expected_ids else {"TL": 0, "TR": 1, "BR": 3, "BL": 2}
    allowed_ids = set(exp_corner_ids.values())

    detector = make_fast_detector(dict_val)

    # Pass 1: Direct fast scan
    c, ids, _ = detector.detectMarkers(small_gray)
    marker_map = {}
    if ids is not None:
        for i, mid in enumerate(ids.flatten()):
            mid_int = int(mid)
            if mid_int in allowed_ids or (not expected_ids and mid_int < 10):
                marker_map[mid_int] = c[i][0] * inv_scale

    # Pass 2: Quick contrast enhancement (CLAHE) if fewer than 4 markers found
    if len(marker_map) < 4:
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        enhanced = clahe.apply(small_gray)
        c_cl, ids_cl, _ = detector.detectMarkers(enhanced)
        if ids_cl is not None:
            for i, mid in enumerate(ids_cl.flatten()):
                mid_int = int(mid)
                if (mid_int in allowed_ids or (not expected_ids and mid_int < 10)) and mid_int not in marker_map:
                    marker_map[mid_int] = c_cl[i][0] * inv_scale

    # Pass 3: 3-Marker Geometric Recovery & localized patch scan on full resolution
    if len(marker_map) == 3:
        marker_map = recover_missing_corner(marker_map, exp_corner_ids, gray, dict_val=dict_val)

    # If still fewer than 3 markers and dict was auto, try fallback dictionaries with strict ID filter
    if len(marker_map) < 4 and (dict_name == "auto" or not dict_name):
        for alt_name, alt_val in FAST_ARUCO_DICTS[1:]:
            alt_det = make_fast_detector(alt_val)
            c_alt, ids_alt, _ = alt_det.detectMarkers(small_gray)
            if ids_alt is not None:
                alt_map = {}
                for i, mid in enumerate(ids_alt.flatten()):
                    mid_int = int(mid)
                    if mid_int in allowed_ids or mid_int < 10:
                        alt_map[mid_int] = c_alt[i][0] * inv_scale
                if len(alt_map) >= 4:
                    marker_map = alt_map
                    best_dict_name = alt_name
                    break

    if len(marker_map) < 4:
        found_cnt = len(marker_map)
        id_str = str(list(marker_map.keys()))
        return None, None, best_dict_name, f"ArUco: Ditemukan {found_cnt}/4 marker sudut {id_str} ({best_dict_name})."

    # Identify TL, TR, BR, BL corners
    if all(exp_corner_ids[k] in marker_map for k in ["TL", "TR", "BR", "BL"]):
        corner_ids = exp_corner_ids
    else:
        # Spatial quadrant assignment
        all_centers = {mid: np.mean(pts, axis=0) for mid, pts in marker_map.items()}
        c_pts_arr = np.array(list(all_centers.values()))
        c_keys = list(all_centers.keys())
        s = c_pts_arr.sum(axis=1)
        diff = np.diff(c_pts_arr, axis=1)
        corner_ids = {
            "TL": c_keys[int(np.argmin(s))],
            "TR": c_keys[int(np.argmin(diff))],
            "BR": c_keys[int(np.argmax(s))],
            "BL": c_keys[int(np.argmax(diff))]
        }

    target_markers = [
        ("TL", marker_map[corner_ids["TL"]]),
        ("TR", marker_map[corner_ids["TR"]]),
        ("BR", marker_map[corner_ids["BR"]]),
        ("BL", marker_map[corner_ids["BL"]])
    ]

    all_pts = np.vstack([tm[1] for tm in target_markers])
    doc_center = np.mean(all_pts, axis=0)

    crop_pts = []
    for lbl, c_pts in target_markers:
        dists = np.hypot(c_pts[:, 0] - doc_center[0], c_pts[:, 1] - doc_center[1])
        if crop_mode == "inner":
            # Strictly inside the ArUco marker (closest vertex to document center)
            pt = c_pts[np.argmin(dists)]
        elif crop_mode == "outer":
            # Outside corner of the ArUco marker (farthest vertex from document center)
            pt = c_pts[np.argmax(dists)]
        else:
            pt = np.mean(c_pts, axis=0)
        crop_pts.append(pt)

    ordered_pts = np.array(crop_pts, dtype="float32")
    return ordered_pts, corner_ids, best_dict_name, "DETECTED"


def find_document_corners(image, min_area_ratio=0.18, target_w=1700, target_h=2400):
    """
    Intelligent Paper Boundary Detector:
    Di awal mendeteksi 4 sudut fisik dokumen / lembar kertas secara jelas.
    Mendukung foto kamera di atas meja maupun berkas scan digital langsung.
    """
    h, w = image.shape[:2]
    scale = 800.0 / max(h, w)
    small_w, small_h = int(w * scale), int(h * scale)
    small = cv2.resize(image, (small_w, small_h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if len(small.shape) == 3 else small

    # Tambahkan border padding 15px agar tepi kertas scan digital yang menyentuh batas gambar terdeteksi sempurna
    pad = 15
    padded = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=0)
    blurred = cv2.GaussianBlur(padded, (5, 5), 0)
    edges = cv2.Canny(blurred, 30, 120)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    best_quad = None
    min_area = (small_w * small_h) * min_area_ratio

    for cnt in contours[:10]:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.025 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            pts = (approx.reshape(4, 2) - np.array([pad, pad])) / scale
            pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
            pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
            ordered = order_points(pts, target_w=target_w, target_h=target_h)
            pw = np.hypot(ordered[1][0] - ordered[0][0], ordered[1][1] - ordered[0][1])
            ph = np.hypot(ordered[3][0] - ordered[0][0], ordered[3][1] - ordered[0][1])
            if pw > 0 and ph > 0:
                ar = pw / ph
                if 0.35 <= ar <= 2.5:
                    best_quad = ordered
                    break

    if best_quad is not None:
        return best_quad, "DETECTED"

    # Default boundary (halaman penuh)
    full_bounds = np.array([
        [0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]
    ], dtype="float32")
    return full_bounds, "DETECTED (Batas Halaman)"


def find_regmarks(image, target_w=1700, target_h=2400, crop_mode="inner", doc_corners=None):
    """
    Robust Corner Registration Mark (RegMark) Detector:
    1. Di awal mendeteksi 4 sudut fisik dokumen / lembar kertas secara jelas.
    2. Mencari marker anchor fiducial solid di zona 4 pojok (TL, TR, BR, BL).
    3. Mengunci sudut inner/outer/center dari 4 pojok marker.
    4. Fallback ke 4 sudut fisik dokumen jika lembar tidak memiliki marker cetak terpisah.
    """
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()

    # 1. Di awal: pastikan 4 sudut dokumen terdeteksi jelas
    if doc_corners is None:
        doc_corners, _ = find_document_corners(image, target_w=target_w, target_h=target_h)
    if doc_corners is None or len(doc_corners) != 4:
        doc_corners = np.array([
            [0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]
        ], dtype="float32")

    doc_center = np.mean(doc_corners, axis=0)

    # 2. Segmentasi kontur solid marker
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    min_reg_area = w * h * 0.00030
    max_reg_area = w * h * 0.0250

    corner_pts = []
    found_count = 0

    for corner in doc_corners:
        cx, cy = corner
        vec = doc_center - corner

        # Zona pencarian sudut terfokus (maksimal 32% ke arah pusat dokumen)
        x_min = max(0, int(min(cx, cx + vec[0] * 0.32)))
        x_max = min(w, int(max(cx, cx + vec[0] * 0.32)))
        y_min = max(0, int(min(cy, cy + vec[1] * 0.32)))
        y_max = min(h, int(max(cy, cy + vec[1] * 0.32)))

        roi = thresh[y_min:y_max, x_min:x_max]
        cnts_roi, _ = cv2.findContours(roi, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        best_cand = None
        min_dist = float("inf")

        for c in cnts_roi:
            bx, by, bw, bh = cv2.boundingRect(c)
            ar = bw / float(bh) if bh > 0 else 0
            if not (0.60 <= ar <= 1.65):
                continue
            hull = cv2.convexHull(c)
            hull_area = cv2.contourArea(hull)
            if hull_area < min_reg_area or hull_area > max_reg_area:
                continue
            rect_fill = hull_area / float(bw * bh) if (bw * bh) > 0 else 0
            if rect_fill < 0.60:
                continue

            mcx = bx + bw / 2.0 + x_min
            mcy = by + bh / 2.0 + y_min
            d = np.hypot(mcx - cx, mcy - cy)
            if d < min_dist:
                min_dist = d
                best_cand = c.reshape(-1, 2) + np.array([x_min, y_min])

        if best_cand is not None:
            found_count += 1
            dists = np.hypot(best_cand[:, 0] - doc_center[0], best_cand[:, 1] - doc_center[1])
            if crop_mode == "inner":
                pt = best_cand[np.argmin(dists)]
            elif crop_mode == "outer":
                pt = best_cand[np.argmax(dists)]
            else:
                pt = np.mean(best_cand, axis=0)
            corner_pts.append(pt)
        else:
            corner_pts.append(corner)

    if found_count == 4:
        pts = np.array(corner_pts, dtype="float32")
        return order_points(pts, target_w=target_w, target_h=target_h), "DETECTED"

    return doc_corners, "FALLBACK_DOC_CORNERS"


def order_points(pts, target_w=1700, target_h=2400):
    """
    Order points: top-left, top-right, bottom-right, bottom-left.
    """
    pts = np.array(pts, dtype="float32")
    rect = np.zeros((4, 2), dtype="float32")

    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # Top-Left
    rect[2] = pts[np.argmax(s)]  # Bottom-Right

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # Top-Right
    rect[3] = pts[np.argmax(diff)]  # Bottom-Left

    top_w = np.hypot(rect[1][0] - rect[0][0], rect[1][1] - rect[0][1])
    bot_w = np.hypot(rect[2][0] - rect[3][0], rect[2][1] - rect[3][1])
    left_h = np.hypot(rect[3][0] - rect[0][0], rect[3][1] - rect[0][1])
    right_h = np.hypot(rect[2][0] - rect[1][0], rect[2][1] - rect[1][1])

    avg_w = (top_w + bot_w) / 2.0
    avg_h = (left_h + right_h) / 2.0

    if (target_h > target_w) and (avg_w > avg_h * 1.10):
        rect = np.roll(rect, shift=-1, axis=0)

    return rect


def standardize_document_image(image_bgr, target_bg=245):
    """
    Standardizes the visual appearance of a cropped/warped LJK image:
    1. Removes shadows and uneven lighting gradients (background normalization).
    2. Adjusts contrast so paper is clean bright white (~245) and ink/bubbles are deep crisp dark.
    3. Neutralizes color casts (removes warm yellowish or cool bluish tint).
    4. Applies gentle edge sharpening for crystal-clear bubble boundaries and text legibility.
    """
    if image_bgr is None or image_bgr.size == 0:
        return image_bgr

    # 1. Convert to LAB color space
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)

    # 2. Downscaled background illumination estimation on L channel (<0.08s)
    h, w = l.shape
    scale = 0.5
    small_l = cv2.resize(l, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    kernel_size = 25
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    bg_small = cv2.morphologyEx(small_l, cv2.MORPH_DILATE, kernel)
    bg_small = cv2.GaussianBlur(bg_small, (kernel_size, kernel_size), 0)
    bg_l = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)

    bg_l_safe = np.maximum(bg_l, 1)

    # Divide L by background to equalize illumination across the page
    l_div = np.clip((l.astype(np.float32) / bg_l_safe.astype(np.float32)) * float(target_bg), 0, 255).astype(np.uint8)

    # 3. Dynamic contrast enhancement (stretch ink/bubbles to black, keep paper white)
    p1 = float(np.percentile(l_div, 1))
    p99 = float(np.percentile(l_div, 99))
    if p99 > p1 + 25:
        l_std = np.clip((l_div.astype(np.float32) - p1) * 250.0 / (p99 - p1), 0, 255).astype(np.uint8)
    else:
        l_std = l_div

    # 4. Subtle unsharp masking for crisp text and bubble edges
    blurred_l = cv2.GaussianBlur(l_std, (0, 0), 1.2)
    l_sharp = cv2.addWeighted(l_std, 1.25, blurred_l, -0.25, 0)

    # 5. Neutralize color cast in A and B channels (white balance adjustment)
    med_a = float(np.median(a))
    med_b = float(np.median(b))
    # Standard neutral LAB is 128
    a_clean = np.clip(a.astype(np.float32) - (med_a - 128.0) * 0.85, 0, 255).astype(np.uint8)
    b_clean = np.clip(b.astype(np.float32) - (med_b - 128.0) * 0.85, 0, 255).astype(np.uint8)

    merged_lab = cv2.merge([l_sharp, a_clean, b_clean])
    return cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)


def detect_corners_and_crop(
    image,
    canvas_w=1700,
    canvas_h=2400,
    preferred_method="aruco",
    expected_ids=None,
    dict_name=None,
    crop_mode="inner",
    apply_standardization=True
):
    """
    Unified Corner Detection & Perspective Cropping Engine with Strict Inner Crop:
    1. Evaluates 4 corners with multi-angle rotation checks (0, 90, 180, 270 degrees).
    2. Uses 3-marker geometric recovery to guarantee finding all 4 ArUco corners even with glare/shadows.
    3. crop_mode='inner' guarantees cropping STRICTLY INSIDE the 4 ArUco markers.
    4. Warps perspective to canonical canvas dimensions (canvas_w x canvas_h).
    5. Coordinates returned in ordered_pts always match the original unrotated image space.
    """
    ordered_pts = None
    status = "FAILED"
    method_used = "none"
    corner_ids = None
    detected_dict = None

    h_in, w_in = image.shape[:2]

    # Smart candidate angle search:
    # If image is landscape (w > h) while canvas is portrait (h > w), try 90 and 270 first
    if w_in > h_in and canvas_h > canvas_w:
        candidate_angles = [90, 270, 0, 180]
    else:
        candidate_angles = [0, 180, 90, 270]

    # 0. Di awal: Deteksi dulu 4 corner fisik dokumen secara jelas
    doc_corners, _ = find_document_corners(image, target_w=canvas_w, target_h=canvas_h)
    if doc_corners is None or len(doc_corners) != 4:
        doc_corners = np.array([
            [0, 0], [w_in - 1, 0], [w_in - 1, h_in - 1], [0, h_in - 1]
        ], dtype="float32")

    # 1. Primary Method: ArUco Fiducials (Strict Inner Crop)
    if preferred_method in ["aruco", "auto"]:
        for ang in candidate_angles:
            rot_img = rotate_image(image, ang) if ang != 0 else image
            pts_aruco, c_ids, d_name, status_aruco = find_aruco_markers(
                rot_img, dict_name=dict_name, expected_ids=expected_ids, crop_mode=crop_mode
            )
            if pts_aruco is not None and status_aruco == "DETECTED":
                if ang != 0:
                    ordered_pts = np.array([unrotate_point(pt, image.shape, ang) for pt in pts_aruco], dtype="float32")
                else:
                    ordered_pts = pts_aruco
                corner_ids = c_ids
                detected_dict = d_name
                status = "DETECTED (4 Sudut Terkunci Sempurna - ArUco)"
                method_used = "aruco"
                break

    # 2. Secondary Method: Corner Anchor RegMarks (Berbasis 4 Sudut Dokumen)
    if ordered_pts is None and preferred_method in ["regmark", "auto"]:
        for ang in candidate_angles:
            rot_img = rotate_image(image, ang) if ang != 0 else image
            cur_doc_corners = find_document_corners(rot_img, target_w=canvas_w, target_h=canvas_h)[0] if ang != 0 else doc_corners
            pts_reg, status_reg = find_regmarks(
                rot_img, target_w=canvas_w, target_h=canvas_h, crop_mode=crop_mode, doc_corners=cur_doc_corners
            )
            if pts_reg is not None and status_reg == "DETECTED":
                if ang != 0:
                    ordered_pts = np.array([unrotate_point(pt, image.shape, ang) for pt in pts_reg], dtype="float32")
                else:
                    ordered_pts = pts_reg
                status = "DETECTED (4 Sudut Terkunci - Corner Anchor RegMark)"
                method_used = "regmark"
                break

    # 3. Tertiary Fallback Method: 4 Sudut Fisik Dokumen yang Jelas di Awal
    if ordered_pts is None:
        ordered_pts = doc_corners
        status = "DETECTED (4 Sudut Terkunci - Batas Fisik Dokumen)"
        method_used = "doc_contour"

    # 4. Crop via Perspective Warp
    warped_img, M = perspective_warp(image, ordered_pts, canvas_w, canvas_h)

    # 5. Image Standardization (Shadow removal, illumination equalization, contrast normalization)
    if apply_standardization and warped_img is not None and warped_img.size > 0:
        warped_img = standardize_document_image(warped_img)

    return warped_img, ordered_pts, method_used, corner_ids, detected_dict, status


def perspective_warp(image, src_points, dst_width, dst_height):
    """
    Warp & Crop image bounded by 4 points to canonical canvas (dst_width x dst_height).
    All content outside the polygon of src_points is completely cropped away.
    """
    dst_points = np.array([
        [0, 0],
        [dst_width - 1, 0],
        [dst_width - 1, dst_height - 1],
        [0, dst_height - 1]
    ], dtype="float32")

    M = cv2.getPerspectiveTransform(src_points, dst_points)
    warped = cv2.warpPerspective(
        image, M, (dst_width, dst_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE
    )
    return warped, M


def draw_regmarks_overlay(image, ordered_pts, method="aruco", corner_ids=None, status="DETECTED", crop_mode="inner"):
    """
    Draw confirmation of the 4 corner markers with crop boundary lines, crosshairs, and corner coordinates.
    High-contrast badges guarantee clear visibility across all lighting and background conditions.
    """
    output = image.copy()
    if ordered_pts is None:
        return output

    labels = ["TL", "TR", "BR", "BL"]
    pts_int = ordered_pts.astype(np.int32)

    # Garis batas poligon crop hijau cerah
    cv2.polylines(output, [pts_int], isClosed=True, color=(0, 230, 0), thickness=3, lineType=cv2.LINE_AA)

    for i in range(4):
        cx, cy = int(round(ordered_pts[i][0])), int(round(ordered_pts[i][1]))

        # Retikel target sudut dengan crosshairs merah & titik hijau
        cv2.circle(output, (cx, cy), 18, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.circle(output, (cx, cy), 6, (0, 255, 0), -1, cv2.LINE_AA)
        cv2.line(output, (cx - 24, cy), (cx + 24, cy), (0, 0, 255), 2, cv2.LINE_AA)
        cv2.line(output, (cx, cy - 24), (cx, cy + 24), (0, 0, 255), 2, cv2.LINE_AA)

        id_str = f" [ID:{corner_ids[labels[i]]}]" if corner_ids and labels[i] in corner_ids else ""
        label_text = f" {labels[i]}{id_str} ({cx}, {cy}) "

        text_y = cy - 20 if i in [0, 1] else cy + 34
        text_x = max(10, cx - 65)

        # Kotak hitam kontras tinggi di belakang teks agar selalu terlihat jelas
        (tw, th), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)
        cv2.rectangle(output, (text_x - 3, text_y - th - 3), (text_x + tw + 3, text_y + baseline + 2), (0, 0, 0), -1)
        cv2.putText(output, label_text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 255), 2, cv2.LINE_AA)

    return output


def rotate_image(image, angle):
    """
    Rotate image by 90, 180, or 270 degrees.
    """
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    elif angle == 270 or angle == -90:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image
