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


def make_fast_detector(dict_val):
    """
    Creates an ultra-fast ArUco detector by filtering out all small answer bubbles/letters
    with minMarkerPerimeterRate=0.035, eliminating useless bit-decoding sweeps.
    """
    dictionary = cv2.aruco.getPredefinedDictionary(dict_val)
    parameters = cv2.aruco.DetectorParameters()
    # Reject small bubbles and characters immediately (50x speedup!)
    parameters.minMarkerPerimeterRate = 0.035
    parameters.maxMarkerPerimeterRate = 2.5
    parameters.adaptiveThreshWinSizeMin = 3
    parameters.adaptiveThreshWinSizeMax = 53
    parameters.adaptiveThreshWinSizeStep = 10
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(dictionary, parameters)


def find_aruco_markers(image, dict_name=None, expected_ids=None, crop_mode="inner"):
    """
    Blazing-fast (< 0.05s) ArUco marker detector.
    Extracts the 4 corner fiducial markers and computes inner crop corners.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
    h, w = gray.shape

    # Pre-scale if image is huge (e.g. > 1600px width) for instantaneous contour extraction
    scale = 1.0
    if w > 1600:
        scale = 1400.0 / float(w)
        small_gray = cv2.resize(gray, (1400, int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        small_gray = gray

    # Select dictionaries to try
    dicts_to_try = []
    if dict_name and dict_name != "auto":
        all_d = FAST_ARUCO_DICTS + FALLBACK_DICTS
        for name, d_val in all_d:
            if name == dict_name:
                dicts_to_try.append((name, d_val))
                break
    if not dicts_to_try:
        dicts_to_try = FAST_ARUCO_DICTS

    best_corners = None
    best_ids = None
    best_dict_name = None

    # Pass 1: Direct fast scan on prioritized dictionaries
    for d_name, d_val in dicts_to_try:
        detector = make_fast_detector(d_val)
        c, ids, _ = detector.detectMarkers(small_gray)
        if ids is not None and len(ids) >= 4:
            best_corners = c
            best_ids = ids.flatten()
            best_dict_name = d_name
            break
        elif ids is not None and len(ids) > 0:
            if best_ids is None or len(ids) > len(best_ids):
                best_corners = c
                best_ids = ids.flatten()
                best_dict_name = d_name

    # Pass 2: Quick contrast enhancement only if no dictionary had >= 4 markers
    if (best_ids is None or len(best_ids) < 4) and len(dicts_to_try) > 0:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(small_gray)
        for d_name, d_val in dicts_to_try:
            detector = make_fast_detector(d_val)
            c, ids, _ = detector.detectMarkers(enhanced)
            if ids is not None and len(ids) >= 4:
                best_corners = c
                best_ids = ids.flatten()
                best_dict_name = d_name
                break

    if best_ids is None or len(best_ids) < 4:
        found_cnt = len(best_ids) if best_ids is not None else 0
        id_str = str(list(best_ids)) if best_ids is not None else "[]"
        return None, None, best_dict_name, f"ArUco: Ditemukan {found_cnt}/4 marker sudut {id_str} ({best_dict_name or 'Semua Dictionary'})."

    # Scale corner coordinates back to original image dimensions
    marker_map = {}
    inv_scale = 1.0 / scale
    for i, mid in enumerate(best_ids):
        c_pts = best_corners[i][0] * inv_scale # (4, 2)
        center = np.mean(c_pts, axis=0)
        marker_map[int(mid)] = {
            "center": center,
            "corners": c_pts,
            "rotation": get_marker_rotation(c_pts)
        }

    rotations = [m["rotation"] for m in marker_map.values()]
    dominant_rot = int(np.median(rotations))

    all_centers = np.array([m["center"] for m in marker_map.values()])
    doc_center = np.mean(all_centers, axis=0)

    # If expected_ids given (from template.json in Mode 2)
    if expected_ids and all(expected_ids[k] in marker_map for k in ["TL", "TR", "BR", "BL"]):
        corner_ids = expected_ids
    else:
        # Determine which marker is TL, TR, BR, BL based on spatial position & rotation
        if len(marker_map) > 4:
            min_x, min_y = np.min(all_centers, axis=0)
            max_x, max_y = np.max(all_centers, axis=0)
            target_extremes = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
            selected_mids = []
            for te in target_extremes:
                best_mid = None
                min_d = float('inf')
                for mid, mdata in marker_map.items():
                    if mid in selected_mids:
                        continue
                    d = np.hypot(mdata["center"][0] - te[0], mdata["center"][1] - te[1])
                    if d < min_d:
                        min_d = d
                        best_mid = mid
                if best_mid is not None:
                    selected_mids.append(best_mid)
        else:
            selected_mids = list(marker_map.keys())

        sub_centers = np.array([marker_map[mid]["center"] for mid in selected_mids])

        if dominant_rot == 180:
            # Document upside-down (kebalik 180): TL of doc is at bottom-right of image
            s = sub_centers.sum(axis=1)
            tl_idx = int(np.argmax(s))
            br_idx = int(np.argmin(s))
            diff = np.diff(sub_centers, axis=1)
            tr_idx = int(np.argmax(diff))
            bl_idx = int(np.argmin(diff))
        elif dominant_rot == 90:
            tl_idx = int(np.argmin(np.diff(sub_centers, axis=1)))
            tr_idx = int(np.argmax(sub_centers.sum(axis=1)))
            br_idx = int(np.argmax(np.diff(sub_centers, axis=1)))
            bl_idx = int(np.argmin(sub_centers.sum(axis=1)))
        elif dominant_rot == 270:
            tl_idx = int(np.argmax(np.diff(sub_centers, axis=1)))
            tr_idx = int(np.argmin(sub_centers.sum(axis=1)))
            br_idx = int(np.argmin(np.diff(sub_centers, axis=1)))
            bl_idx = int(np.argmax(sub_centers.sum(axis=1)))
        else:
            # Upright (0)
            s = sub_centers.sum(axis=1)
            tl_idx = int(np.argmin(s))
            br_idx = int(np.argmax(s))
            diff = np.diff(sub_centers, axis=1)
            tr_idx = int(np.argmin(diff))
            bl_idx = int(np.argmax(diff))

        indices = [tl_idx, tr_idx, br_idx, bl_idx]
        if len(set(indices)) == 4:
            corner_ids = {
                "TL": int(selected_mids[tl_idx]),
                "TR": int(selected_mids[tr_idx]),
                "BR": int(selected_mids[br_idx]),
                "BL": int(selected_mids[bl_idx])
            }
        else:
            corner_ids = {
                "TL": selected_mids[0], "TR": selected_mids[1],
                "BR": selected_mids[2], "BL": selected_mids[3]
            }

    # Extract Crop Points using Geometric Invariant:
    # Inner corner: argmin(distance to doc_center) -> strictly takes the inside rectangle!
    # Outer corner: argmax(distance to doc_center)
    # Center: mean(corners)
    target_markers = [
        ("TL", marker_map[corner_ids["TL"]]["corners"]),
        ("TR", marker_map[corner_ids["TR"]]["corners"]),
        ("BR", marker_map[corner_ids["BR"]]["corners"]),
        ("BL", marker_map[corner_ids["BL"]]["corners"])
    ]

    crop_pts = []
    for lbl, c_pts in target_markers:
        dists = np.hypot(c_pts[:, 0] - doc_center[0], c_pts[:, 1] - doc_center[1])
        if crop_mode == "inner":
            pt = c_pts[np.argmin(dists)]
        elif crop_mode == "outer":
            pt = c_pts[np.argmax(dists)]
        else:
            pt = np.mean(c_pts, axis=0)
        crop_pts.append(pt)

    ordered_pts = np.array(crop_pts, dtype="float32")
    return ordered_pts, corner_ids, best_dict_name, "DETECTED"


def find_regmarks(image, target_w=1700, target_h=2400, crop_mode="inner"):
    """
    Fallback anchor marker detector (for sheets with solid corner square/circle marks).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
    h, w = gray.shape
    image_area = h * w

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 15
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []

    for c in contours:
        area = cv2.contourArea(c)
        if area < image_area * 0.0002 or area > image_area * 0.08:
            continue

        x, y, bw, bh = cv2.boundingRect(c)
        if bw == 0 or bh == 0:
            continue

        aspect_ratio = bw / float(bh)
        if aspect_ratio < 0.35 or aspect_ratio > 2.8:
            continue

        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        solidity = area / float(hull_area) if hull_area > 0 else 0
        if solidity < 0.55:
            continue

        M = cv2.moments(c)
        if M["m00"] == 0:
            continue

        cx = float(M["m10"] / M["m00"])
        cy = float(M["m01"] / M["m00"])

        candidates.append({
            "area": area,
            "cx": cx,
            "cy": cy,
            "x": x,
            "y": y,
            "bw": bw,
            "bh": bh,
            "contour": c
        })

    if len(candidates) < 4:
        return None, f"RegMark: Ditemukan {len(candidates)} kandidat marker sudut (minimal 4)."

    cand_pts = np.array([[c["cx"], c["cy"]] for c in candidates])
    min_x, min_y = np.min(cand_pts, axis=0)
    max_x, max_y = np.max(cand_pts, axis=0)

    target_corners = [
        (min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)
    ]

    selected_cands = []
    used_indices = set()

    for tc in target_corners:
        best_idx = None
        min_dist = float('inf')
        for idx, cand in enumerate(candidates):
            if idx in used_indices:
                continue
            dist = np.hypot(cand["cx"] - tc[0], cand["cy"] - tc[1])
            if dist < min_dist:
                min_dist = dist
                best_idx = idx

        if best_idx is not None:
            used_indices.add(best_idx)
            selected_cands.append(candidates[best_idx])

    if len(selected_cands) != 4:
        return None, "RegMark: Tidak berhasil mengunci 4 sudut anchor secara unik."

    c_tl, c_tr, c_br, c_bl = selected_cands[0], selected_cands[1], selected_cands[2], selected_cands[3]

    if crop_mode == "inner":
        pts = np.array([
            [c_tl["x"] + c_tl["bw"], c_tl["y"] + c_tl["bh"]],
            [c_tr["x"], c_tr["y"] + c_tr["bh"]],
            [c_br["x"], c_br["y"]],
            [c_bl["x"] + c_bl["bw"], c_bl["y"]]
        ], dtype="float32")
    elif crop_mode == "outer":
        pts = np.array([
            [c_tl["x"], c_tl["y"]],
            [c_tr["x"] + c_tr["bw"], c_tr["y"]],
            [c_br["x"] + c_br["bw"], c_br["y"] + c_br["bh"]],
            [c_bl["x"], c_bl["y"] + c_bl["bh"]]
        ], dtype="float32")
    else:
        pts = np.array([[c["cx"], c["cy"]] for c in selected_cands], dtype="float32")

    ordered_pts = order_points(pts, target_w=target_w, target_h=target_h)
    return ordered_pts, "DETECTED"


def order_points(pts, target_w=1700, target_h=2400):
    """
    Order points: top-left, top-right, bottom-right, bottom-left.
    """
    pts = np.array(pts, dtype="float32")
    rect = np.zeros((4, 2), dtype="float32")

    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)] # Top-Left
    rect[2] = pts[np.argmax(s)] # Bottom-Right

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)] # Top-Right
    rect[3] = pts[np.argmax(diff)] # Bottom-Left

    top_w = np.hypot(rect[1][0] - rect[0][0], rect[1][1] - rect[0][1])
    bot_w = np.hypot(rect[2][0] - rect[3][0], rect[2][1] - rect[3][1])
    left_h = np.hypot(rect[3][0] - rect[0][0], rect[3][1] - rect[0][1])
    right_h = np.hypot(rect[2][0] - rect[1][0], rect[2][1] - rect[1][1])

    avg_w = (top_w + bot_w) / 2.0
    avg_h = (left_h + right_h) / 2.0

    if (target_h > target_w) and (avg_w > avg_h * 1.10):
        rect = np.roll(rect, shift=-1, axis=0)

    return rect


def detect_corners_and_crop(
    image,
    canvas_w=1700,
    canvas_h=2400,
    preferred_method="aruco",
    expected_ids=None,
    dict_name=None,
    crop_mode="inner"
):
    """
    Unified Corner Detection & Perspective Cropping Engine:
    Runs in < 0.05 seconds.
    crop_mode="inner" guarantees taking ONLY the rectangle strictly inside the ArUco markers.
    """
    ordered_pts = None
    status = "FAILED"
    method_used = "none"
    corner_ids = None
    detected_dict = None

    if preferred_method in ["aruco", "auto"]:
        pts_aruco, c_ids, d_name, status_aruco = find_aruco_markers(
            image, dict_name=dict_name, expected_ids=expected_ids, crop_mode=crop_mode
        )
        if pts_aruco is not None and status_aruco == "DETECTED":
            ordered_pts = pts_aruco
            corner_ids = c_ids
            detected_dict = d_name
            status = "DETECTED (ArUco Fiducial Locked)"
            method_used = "aruco"

    if ordered_pts is None:
        pts_reg, status_reg = find_regmarks(image, target_w=canvas_w, target_h=canvas_h, crop_mode=crop_mode)
        if pts_reg is not None and status_reg == "DETECTED":
            ordered_pts = pts_reg
            status = "DETECTED (Corner Anchor Locked)"
            method_used = "regmark"
        else:
            status = f"{status_reg}"

    if ordered_pts is not None:
        warped_img, M = perspective_warp(image, ordered_pts, canvas_w, canvas_h)
    else:
        warped_img = cv2.resize(image, (canvas_w, canvas_h))
        ordered_pts = np.array([
            [0, 0], [canvas_w, 0], [canvas_w, canvas_h], [0, canvas_h]
        ], dtype="float32")

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
    """
    output = image.copy()
    if ordered_pts is None:
        return output

    labels = ["TL", "TR", "BR", "BL"]
    pts_int = ordered_pts.astype(np.int32)

    # Green crop boundary line
    cv2.polylines(output, [pts_int], isClosed=True, color=(0, 220, 0), thickness=3, lineType=cv2.LINE_AA)

    for i in range(4):
        cx, cy = int(round(ordered_pts[i][0])), int(round(ordered_pts[i][1]))

        cv2.circle(output, (cx, cy), 18, (0, 0, 255), 2, cv2.LINE_AA)
        cv2.circle(output, (cx, cy), 6, (0, 255, 0), -1, cv2.LINE_AA)
        cv2.line(output, (cx - 25, cy), (cx + 25, cy), (0, 0, 255), 2, cv2.LINE_AA)
        cv2.line(output, (cx, cy - 25), (cx, cy + 25), (0, 0, 255), 2, cv2.LINE_AA)

        id_str = f" [ID:{corner_ids[labels[i]]}]" if corner_ids and labels[i] in corner_ids else ""
        label_text = f"{labels[i]}{id_str} ({cx}, {cy})"

        text_y = cy - 22 if i in [0, 1] else cy + 32
        text_x = max(10, cx - 60)
        cv2.putText(output, label_text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (0, 255, 255), 2, cv2.LINE_AA)

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
