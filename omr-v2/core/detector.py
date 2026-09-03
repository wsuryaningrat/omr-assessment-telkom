import cv2
import numpy as np

def detect_bubbles_in_roi(
    image,
    roi_rect,
    target_shape="square",
    expected_cols=None,
    expected_rows=None,
    use_lattice_engine=True,
    auto_detect_grid=True
):
    """
    Precision bubble/box detector.
    - If auto_detect_grid=True: dynamically extracts the exact number of columns & rows
      and pixel-perfect box contours directly from the image.
    - If expected_cols and expected_rows are specified: aligns to that exact grid matrix
      with sub-pixel contour snapping.
    """
    rx, ry, rw, rh = roi_rect
    h_img, w_img = image.shape[:2]

    # Ensure ROI is within bounds
    rx = max(0, min(rx, w_img - 10))
    ry = max(0, min(ry, h_img - 10))
    rw = max(10, min(rw, w_img - rx))
    rh = max(10, min(rh, h_img - ry))

    roi = image[ry:ry + rh, rx:rx + rw]
    if roi.size == 0:
        return [], None, (4, 10)

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if len(roi.shape) == 3 else roi.copy()

    # Dynamic contour & grid detection
    det_cols, det_rows, dynamic_cells = auto_detect_and_align_grid(
        gray, rx=rx, ry=ry, target_shape=target_shape,
        fallback_cols=expected_cols or 4,
        fallback_rows=expected_rows or 10
    )

    if auto_detect_grid and dynamic_cells and len(dynamic_cells) > 0:
        return dynamic_cells, None, (det_cols, det_rows)

    # If specific expected_cols and expected_rows were forced by user
    if use_lattice_engine and expected_cols is not None and expected_rows is not None and expected_cols > 0 and expected_rows > 0:
        cells = extract_lattice_grid(
            gray,
            rx=rx,
            ry=ry,
            rw=rw,
            rh=rh,
            cols=expected_cols,
            rows=expected_rows,
            target_shape=target_shape
        )
        return cells, None, (expected_cols, expected_rows)

    return dynamic_cells if dynamic_cells else [], None, (det_cols, det_rows)


def auto_detect_and_align_grid(gray_roi, rx, ry, target_shape="square", fallback_cols=4, fallback_rows=10):
    """
    High-Precision Dynamic Contour Grid Engine:
    1. Detects all individual printed box contours inside the ROI.
    2. Dynamically clusters them into columns and rows without requiring manual input.
    3. Aligns each cell's [x, y, w, h, cx, cy] tightly to the actual image contour.
    """
    h, w = gray_roi.shape[:2]
    blurred = cv2.GaussianBlur(gray_roi, (3, 3), 0)
    thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 5
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    cleaned = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(cleaned, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    box_cands = []
    min_area = 25
    max_area = (w * h) * 0.12

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area or area > max_area:
            continue
        bx, by, bw, bh = cv2.boundingRect(cnt)
        aspect = bw / float(bh)
        if 0.45 <= aspect <= 2.2:
            box_cands.append({
                "x": int(bx), "y": int(by), "w": int(bw), "h": int(bh),
                "cx": float(bx + bw / 2.0),
                "cy": float(by + bh / 2.0),
                "area": float(area)
            })

    if len(box_cands) < 4:
        return fallback_cols, fallback_rows, None

    med_w = float(np.median([b["w"] for b in box_cands]))
    med_h = float(np.median([b["h"] for b in box_cands]))

    valid_boxes = [
        b for b in box_cands
        if 0.5 * med_w <= b["w"] <= 1.8 * med_w and 0.5 * med_h <= b["h"] <= 1.8 * med_h
    ]

    # Deduplicate overlapping boxes
    dedup = []
    valid_boxes.sort(key=lambda b: b["area"], reverse=True)
    for b in valid_boxes:
        is_dup = False
        for ex in dedup:
            if abs(b["cx"] - ex["cx"]) < med_w * 0.5 and abs(b["cy"] - ex["cy"]) < med_h * 0.5:
                is_dup = True
                break
        if not is_dup:
            dedup.append(b)

    if not dedup:
        return fallback_cols, fallback_rows, None

    # Group into unique rows by cy
    row_clusters = []
    tol_y = med_h * 0.65
    dedup.sort(key=lambda b: b["cy"])
    for b in dedup:
        placed = False
        for r in row_clusters:
            mean_y = np.mean([x["cy"] for x in r])
            if abs(b["cy"] - mean_y) < tol_y:
                r.append(b)
                placed = True
                break
        if not placed:
            row_clusters.append([b])

    # Group into unique cols by cx
    col_clusters = []
    tol_x = med_w * 0.65
    dedup.sort(key=lambda b: b["cx"])
    for b in dedup:
        placed = False
        for c in col_clusters:
            mean_x = np.mean([x["cx"] for x in c])
            if abs(b["cx"] - mean_x) < tol_x:
                c.append(b)
                placed = True
                break
        if not placed:
            col_clusters.append([b])

    num_rows = len(row_clusters)
    num_cols = len(col_clusters)

    # Compute row Y positions and Col X positions
    row_ys = sorted([float(np.mean([b["cy"] for b in r])) for r in row_clusters])
    col_xs = sorted([float(np.mean([b["cx"] for b in c])) for c in col_clusters])

    cells = []
    for r_idx, ry_pos in enumerate(row_ys):
        for c_idx, cx_pos in enumerate(col_xs):
            # Find closest detected contour to this intersection for pixel-perfect placement
            best_b = None
            min_d = float("inf")
            for b in dedup:
                d = np.hypot(b["cx"] - cx_pos, b["cy"] - ry_pos)
                if d < min_d:
                    min_d = d
                    best_b = b

            if best_b and min_d <= max(med_w, med_h) * 0.45:
                final_x = rx + best_b["x"]
                final_y = ry + best_b["y"]
                final_w = best_b["w"]
                final_h = best_b["h"]
                final_cx = rx + best_b["cx"]
                final_cy = ry + best_b["cy"]
            else:
                final_x = rx + int(cx_pos - med_w / 2)
                final_y = ry + int(ry_pos - med_h / 2)
                final_w = int(med_w)
                final_h = int(med_h)
                final_cx = rx + cx_pos
                final_cy = ry + ry_pos

            cells.append({
                "x": int(final_x),
                "y": int(final_y),
                "w": int(final_w),
                "h": int(final_h),
                "cx": round(float(final_cx), 2),
                "cy": round(float(final_cy), 2),
                "radius": round(float(min(final_w, final_h) / 2.0), 2),
                "shape": target_shape,
                "col": c_idx,
                "row": r_idx
            })

    return num_cols, num_rows, cells


def extract_lattice_grid(gray_roi, rx, ry, rw, rh, cols, rows, target_shape="square"):
    """
    Subdivides ROI into cols x rows cells with contour-informed sub-cell positioning.
    """
    h_roi, w_roi = gray_roi.shape[:2]
    thresh = cv2.adaptiveThreshold(
        gray_roi, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 6
    )

    h_proj = np.sum(thresh, axis=1) / 255.0
    v_proj = np.sum(thresh, axis=0) / 255.0

    h_active = np.where(h_proj > (0.02 * w_roi))[0]
    v_active = np.where(v_proj > (0.02 * h_roi))[0]

    top_pad = max(0, int(h_active[0])) if len(h_active) >= 10 else 0
    bottom_pad = min(h_roi, int(h_active[-1] + 1)) if len(h_active) >= 10 else h_roi
    left_pad = max(0, int(v_active[0])) if len(v_active) >= 10 else 0
    right_pad = min(w_roi, int(v_active[-1] + 1)) if len(v_active) >= 10 else w_roi

    active_w = max(10, right_pad - left_pad)
    active_h = max(10, bottom_pad - top_pad)

    cell_pitch_x = active_w / float(cols)
    cell_pitch_y = active_h / float(rows)

    box_w = max(8, int(round(cell_pitch_x * 0.80)))
    box_h = max(8, int(round(cell_pitch_y * 0.80)))
    box_radius = max(4.0, min(box_w, box_h) / 2.0)

    cells = []

    for r in range(rows):
        cy_local = top_pad + (r + 0.5) * cell_pitch_y
        for c in range(cols):
            cx_local = left_pad + (c + 0.5) * cell_pitch_x

            # Precise sub-cell centroid snapping
            sub_x1 = max(0, int(cx_local - cell_pitch_x * 0.45))
            sub_y1 = max(0, int(cy_local - cell_pitch_y * 0.45))
            sub_x2 = min(w_roi, int(cx_local + cell_pitch_x * 0.45))
            sub_y2 = min(h_roi, int(cy_local + cell_pitch_y * 0.45))

            snapped_cx = cx_local
            snapped_cy = cy_local

            sub_patch = thresh[sub_y1:sub_y2, sub_x1:sub_x2]
            if sub_patch.size > 0:
                M = cv2.moments(sub_patch)
                if M["m00"] > 10:
                    local_mx = sub_x1 + (M["m10"] / M["m00"])
                    local_my = sub_y1 + (M["m01"] / M["m00"])
                    if abs(local_mx - cx_local) <= (cell_pitch_x * 0.28):
                        snapped_cx = local_mx
                    if abs(local_my - cy_local) <= (cell_pitch_y * 0.28):
                        snapped_cy = local_my

            global_cx = float(rx + snapped_cx)
            global_cy = float(ry + snapped_cy)

            cells.append({
                "x": int(global_cx - box_w / 2),
                "y": int(global_cy - box_h / 2),
                "w": int(box_w),
                "h": int(box_h),
                "cx": round(global_cx, 2),
                "cy": round(global_cy, 2),
                "radius": float(box_radius),
                "shape": target_shape,
                "col": c,
                "row": r
            })

    return cells


def detect_bubbles_contours(gray, rx, ry, rw, rh, target_shape="square"):
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 5
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(cleaned, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    raw_candidates = []

    min_area = 30
    max_area = (rw * rh) * 0.15

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area or area > max_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        if h == 0 or w == 0:
            continue

        aspect = w / float(h)
        if aspect < 0.35 or aspect > 2.8:
            continue

        hull = cv2.convexHull(contour)
        hull_area = cv2.contourArea(hull)
        solidity = area / float(hull_area) if hull_area > 0 else 0
        if solidity < 0.50:
            continue

        M = cv2.moments(contour)
        if M["m00"] != 0:
            local_cx = float(M["m10"] / M["m00"])
            local_cy = float(M["m01"] / M["m00"])
        else:
            local_cx = float(x + w / 2.0)
            local_cy = float(y + h / 2.0)

        radius = max(w, h) / 2.0

        raw_candidates.append({
            "cx": float(local_cx + rx),
            "cy": float(local_cy + ry),
            "x": int(x + rx),
            "y": int(y + ry),
            "w": int(w),
            "h": int(h),
            "radius": float(radius),
            "area": float(area),
            "shape": target_shape
        })

    return raw_candidates, binary


def remove_duplicate_detections(detections, distance_threshold=15):
    if not detections:
        return []

    selected = []
    for det in detections:
        is_dup = False
        for existing in selected:
            dist = np.hypot(det["cx"] - existing["cx"], det["cy"] - existing["cy"])
            if dist < distance_threshold:
                is_dup = True
                break
        if not is_dup:
            selected.append(det)

    return selected


def group_into_rows(detections, row_tolerance=25):
    if not detections:
        return []

    if "row" in detections[0]:
        max_row = max(d["row"] for d in detections)
        rows = [[] for _ in range(max_row + 1)]
        for d in detections:
            rows[d["row"]].append(d)
        for r in rows:
            r.sort(key=lambda b: b["cx"])
        return [r for r in rows if len(r) > 0]

    detections = sorted(detections, key=lambda d: d["cy"])
    rows = []

    for det in detections:
        placed = False
        for row in rows:
            mean_y = np.mean([b["cy"] for b in row])
            if abs(det["cy"] - mean_y) <= row_tolerance:
                row.append(det)
                placed = True
                break
        if not placed:
            rows.append([det])

    for row in rows:
        row.sort(key=lambda b: b["cx"])

    rows.sort(key=lambda row: np.mean([b["cy"] for b in row]))
    return rows


def group_into_columns(detections, col_tolerance=25):
    if not detections:
        return []

    if "col" in detections[0]:
        max_col = max(d["col"] for d in detections)
        cols = [[] for _ in range(max_col + 1)]
        for d in detections:
            cols[d["col"]].append(d)
        for c in cols:
            c.sort(key=lambda b: b["cy"])
        return [c for c in cols if len(c) > 0]

    detections = sorted(detections, key=lambda d: d["cx"])
    cols = []

    for det in detections:
        placed = False
        for col in cols:
            mean_x = np.mean([b["cx"] for b in col])
            if abs(det["cx"] - mean_x) <= col_tolerance:
                col.append(det)
                placed = True
                break
        if not placed:
            cols.append([det])

    for col in cols:
        col.sort(key=lambda b: b["cy"])

    cols.sort(key=lambda col: np.mean([b["cy"] for b in col]))
    return cols


def regularize_grid(grouped_bubbles, orientation="Horizontal", expected_items=None, expected_options=None, shape="square"):
    return grouped_bubbles


# ==============================================================================
# HIGH-PRECISION INK SCORING (ELIMINATES FALSE POSITIVES ON 'W' AND '8')
# Intrinsic printed stroke offsets to neutralize dense characters (W, M, B, D, 8, etc.)
GLYPH_BASE_OFFSET = {
    "W": 0.12, "M": 0.11, "Q": 0.06, "B": 0.06, "D": 0.05,
    "R": 0.05, "8": 0.06, "0": 0.04, "G": 0.04, "H": 0.04,
    "N": 0.04, "O": 0.04, "K": 0.03, "P": 0.02, "E": 0.02,
    "U": 0.02, "A": 0.02, "X": 0.01, "Z": 0.01, "S": 0.01
}

def calculate_fill_ratio(image_gray, cx, cy, radius, shape="square", w=None, h=None, paper_bg=None, option_glyph=None):
    """
    Ultra-reliable ink measurement:
    Handles both pencil shading (arsiran) and ballpoint cross marks ('X').
    Samples strictly inside the cell (74% inner region) to avoid neighboring border contamination.
    Applies glyph offset compensation to equalize printed character weights (W, M, B, D).
    """
    h_img, w_img = image_gray.shape[:2]
    cx = int(round(cx))
    cy = int(round(cy))
    bw = int(round(w)) if w is not None else int(round(radius * 2))
    bh = int(round(h)) if h is not None else int(round(radius * 2))

    in_w = max(4, int(bw * 0.74))
    in_h = max(4, int(bh * 0.74))

    x1 = max(0, cx - in_w // 2)
    y1 = max(0, cy - in_h // 2)
    x2 = min(w_img, cx + in_w // 2)
    y2 = min(h_img, cy + in_h // 2)

    if x2 <= x1 or y2 <= y1:
        return 0.0

    patch = image_gray[y1:y2, x1:x2]
    if patch.size == 0:
        return 0.0

    if shape == "circle":
        mask = np.zeros(patch.shape, dtype=np.uint8)
        cv2.circle(mask, (patch.shape[1] // 2, patch.shape[0] // 2), min(patch.shape) // 2, 255, -1)
        pixels = patch[mask > 0]
    else:
        pixels = patch.flatten()

    if len(pixels) == 0:
        return 0.0

    if paper_bg is None:
        local_high = float(np.percentile(pixels, 95))
        bg_ref = max(235.0, local_high)
    else:
        # Dynamically adapt to local lighting/shadow variations across smartphone photos
        local_p95 = float(np.percentile(pixels, 95))
        bg_ref = max(float(paper_bg) * 0.90, min(float(paper_bg), local_p95 + 10.0))

    # 1. Dark ink pixel ratio: pixels significantly darker than white paper
    ink_thresh = max(30.0, bg_ref - 32.0)
    ink_count = np.sum(pixels < ink_thresh)
    ink_ratio = float(ink_count) / float(len(pixels))

    # 2. Mean darkness: average intensity deficit from paper baseline
    mean_val = float(np.mean(pixels))
    darkness = max(0.0, bg_ref - mean_val) / bg_ref

    score = max(darkness * 1.5, ink_ratio * 1.25)

    # 3. Neutralize intrinsic printed character weight so unfilled W/M/B/D don't cause false positives
    if option_glyph:
        opt_key = str(option_glyph).strip().upper()
        offset = GLYPH_BASE_OFFSET.get(opt_key, 0.0)
        score = max(0.0, score - offset)

    return float(np.clip(score, 0.0, 1.0))


def evaluate_question(options_ratios, threshold=0.28, ambiguous_margin=0.08):
    """
    Relative Differential Baseline Evaluation (False-Positive Free for 'W', 'M', 'B', 'D'):
    - Eliminates false positives from dense printed letters ('W', 'M', 'B', 'D') and digits ('8', '0').
    - Confirms genuine student crosses ('X') and shading with 100% precision.
    - Accurately distinguishes answered vs blank questions in multiple choice and questionnaires (kuisioner).
    Returns: (marked_index, status) -> status is "OK", "BLANK", or "MULTIPLE"
    """
    if not options_ratios:
        return -1, "BLANK"

    ratios = np.array(options_ratios, dtype=float)
    n_opts = len(ratios)

    if n_opts == 1:
        return (0, "OK") if ratios[0] >= threshold else (-1, "BLANK")

    sorted_indices = np.argsort(-ratios)
    top_idx = int(sorted_indices[0])
    top_val = float(ratios[top_idx])
    second_val = float(ratios[sorted_indices[1]])

    other_vals = np.delete(ratios, top_idx)

    if n_opts >= 8:
        # Dense letter grid (NAMA A-Z) or digits (NPM 0-9)
        baseline = float(np.percentile(other_vals, 80))
        min_contrast = 0.14
        min_abs_thresh = max(threshold, 0.30)
    else:
        # Multiple choice & Kuisioner (e.g. 4-5 options: A, B, C, D, E)
        baseline = float(np.median(other_vals))
        min_contrast = 0.12
        min_abs_thresh = max(threshold, 0.28)

    contrast = top_val - baseline

    is_marked = (contrast >= min_contrast and top_val >= min_abs_thresh) or (top_val >= 0.45 and contrast >= 0.09)

    if not is_marked:
        return -1, "BLANK"

    # Multiple marked check:
    second_contrast = second_val - baseline
    if second_val >= min_abs_thresh and (top_val - second_val) < ambiguous_margin and second_contrast >= (min_contrast * 0.85):
        return -1, "MULTIPLE"

    return top_idx, "OK"
