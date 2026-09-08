import cv2
import numpy as np
import pandas as pd
import json

def draw_field_overlay(image, items, orientation="Horizontal", color=(0, 0, 255), show_labels=False, draw_outer_box=True):
    """
    Renders pixel-perfect red box outlines for all grid cells (matching student answer sheet layout).
    """
    output = image.copy()
    if not items:
        return output

    all_bubbles = [b for it in items for b in it.get("bubbles", [])]
    if not all_bubbles:
        return output

    if draw_outer_box and all_bubbles:
        all_x1 = [b.get("x", b["cx"] - b.get("w", 24) / 2) for b in all_bubbles]
        all_y1 = [b.get("y", b["cy"] - b.get("h", 24) / 2) for b in all_bubbles]
        all_x2 = [b.get("x", b["cx"] - b.get("w", 24) / 2) + b.get("w", 24) for b in all_bubbles]
        all_y2 = [b.get("y", b["cy"] - b.get("h", 24) / 2) + b.get("h", 24) for b in all_bubbles]

        rx1 = max(0, int(min(all_x1)) - 3)
        ry1 = max(0, int(min(all_y1)) - 3)
        rx2 = min(image.shape[1], int(max(all_x2)) + 3)
        ry2 = min(image.shape[0], int(max(all_y2)) + 3)

        cv2.rectangle(output, (rx1, ry1), (rx2, ry2), (0, 165, 255), 2)

    for item in items:
        bubbles = item.get("bubbles", [])
        for b in bubbles:
            cx = int(round(b["cx"]))
            cy = int(round(b["cy"]))
            r = int(round(b.get("radius", 12)))
            bw = int(round(b.get("w", r * 2)))
            bh = int(round(b.get("h", r * 2)))
            shape = b.get("shape", "square")

            if "x" in b and "y" in b:
                x1 = int(round(b["x"]))
                y1 = int(round(b["y"]))
                x2 = x1 + bw
                y2 = y1 + bh
            else:
                x1 = cx - bw // 2
                y1 = cy - bh // 2
                x2 = x1 + bw
                y2 = y1 + bh

            if shape == "square":
                cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
            else:
                cv2.circle(output, (cx, cy), r, color, 2)

            if show_labels and "option" in b:
                opt_str = str(b["option"])
                font_scale = 0.35 if len(opt_str) <= 2 else 0.28
                cv2.putText(
                    output,
                    opt_str,
                    (cx - 5, cy + 4),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    (255, 0, 0),
                    1,
                    cv2.LINE_AA
                )

    return output


def draw_all_fields_overlay(image, fields_dict):
    """
    Renders visual overlay for all calibrated fields on the template canvas.
    """
    output = image.copy()
    colors = [
        (0, 0, 255),    # Red
        (0, 180, 0),    # Green
        (255, 0, 0),    # Blue
        (0, 165, 255),  # Orange
        (200, 0, 200),  # Purple
        (0, 215, 255),  # Yellow
        (180, 180, 0),  # Cyan
    ]

    for idx, (fname, fdef) in enumerate(fields_dict.items()):
        color = colors[idx % len(colors)]
        if "roi" in fdef and fdef["roi"]:
            rx, ry, rw, rh = fdef["roi"]
            cv2.rectangle(output, (int(rx), int(ry)), (int(rx + rw), int(ry + rh)), color, 2)
            cv2.putText(
                output,
                fname,
                (int(rx) + 5, max(25, int(ry) - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2,
                cv2.LINE_AA
            )

        items = fdef.get("items", [])
        output = draw_field_overlay(output, items, fdef.get("orientation", "Horizontal"), color=color, show_labels=False, draw_outer_box=True)

    return output


def draw_reading_overlay(image, fields_dict, gray_img, thresh=0.28, margin=0.08):
    """
    High-visibility OMR reading overlay:
    - BOLD GREEN outline and center highlight for marked bubbles/boxes (Cross 'X' or Shading).
    - BOLD RED outline for multiple markings.
    - Clean faint outline for blank bubbles.
    """
    from core.detector import calculate_fill_ratio, evaluate_question
    output = image.copy()

    # Estimate page background brightness
    paper_bg = float(np.percentile(gray_img, 92))
    if paper_bg < 150:
        paper_bg = 240.0

    for f_idx, (fname, fdef) in enumerate(fields_dict.items()):
        items = fdef.get("items", [])
        for it in items:
            bubbles = it.get("bubbles", [])
            ratios = []
            for b in bubbles:
                ratio = calculate_fill_ratio(
                    gray_img,
                    b["cx"],
                    b["cy"],
                    b.get("radius", 12),
                    shape=b.get("shape", "square"),
                    w=b.get("w"),
                    h=b.get("h"),
                    paper_bg=paper_bg,
                    option_glyph=b.get("option")
                )
                ratios.append(ratio)

            # Evaluate which bubble is marked
            marked_idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)

            # Find competitor indices if MULTIPLE
            multiple_indices = set()
            if status == "MULTIPLE" and len(ratios) > 1:
                sorted_idx = np.argsort(-np.array(ratios))
                top_v = ratios[sorted_idx[0]]
                for si in sorted_idx:
                    if (top_v - ratios[si]) < 0.10 and ratios[si] >= min(thresh, 0.18):
                        multiple_indices.add(si)

            for b_i, b in enumerate(bubbles):
                cx = int(round(b["cx"]))
                cy = int(round(b["cy"]))
                r = int(round(b.get("radius", 12)))
                bw = int(round(b.get("w", r * 2)))
                bh = int(round(b.get("h", r * 2)))
                shape = b.get("shape", "square")

                if "x" in b and "y" in b:
                    x1 = int(round(b["x"]))
                    y1 = int(round(b["y"]))
                    x2 = x1 + bw
                    y2 = y1 + bh
                else:
                    x1 = cx - bw // 2
                    y1 = cy - bh // 2
                    x2 = x1 + bw
                    y2 = y1 + bh

                is_selected = (status == "OK" and b_i == marked_idx)
                is_multiple = (b_i in multiple_indices)

                if is_selected:
                    # Bold vibrant GREEN
                    draw_color = (0, 230, 0)
                    thickness = 3
                    if shape == "square":
                        cv2.rectangle(output, (x1, y1), (x2, y2), draw_color, thickness)
                        cv2.circle(output, (cx, cy), max(3, bw // 5), draw_color, -1)
                    else:
                        cv2.circle(output, (cx, cy), r, draw_color, thickness)
                        cv2.circle(output, (cx, cy), max(3, r // 3), draw_color, -1)
                elif is_multiple:
                    # Bold RED
                    draw_color = (0, 0, 255)
                    thickness = 3
                    if shape == "square":
                        cv2.rectangle(output, (x1, y1), (x2, y2), draw_color, thickness)
                    else:
                        cv2.circle(output, (cx, cy), r, draw_color, thickness)
                else:
                    # Faint gray for blanks
                    draw_color = (190, 190, 190)
                    thickness = 1
                    if shape == "square":
                        cv2.rectangle(output, (x1, y1), (x2, y2), draw_color, thickness)
                    else:
                        cv2.circle(output, (cx, cy), r, draw_color, thickness)

    return output


def export_to_csv(results):
    df = pd.DataFrame(results)
    return df.to_csv(index=False).encode("utf-8")


def export_to_json(results):
    return json.dumps(results, indent=2).encode("utf-8")


def evaluate_template_bubble_alignment(gray_img, fields_dict, search_radius=22):
    """
    Mandatory coordinate transformation validation:
    Evaluates alignment between ground-truth JSON template bubble centers and
    physical bubble centers detected in the canonical (1700x2400) image.

    Returns:
    --------
    summary : dict
        Aggregated statistics (median, mean, std, min, max, by_field) of delta (dx, dy).
    bubble_deltas : list[dict]
        Per-bubble delta record: {bubble_id, field, expected, detected, dx, dy, dist}
    """
    if gray_img is None or gray_img.size == 0 or not fields_dict:
        return {"status": "EMPTY", "matched_count": 0, "total_count": 0}, []

    h, w = gray_img.shape[:2]
    all_dx = []
    all_dy = []
    bubble_deltas = []
    field_summaries = {}
    total_bubbles = 0

    for fname, fdef in fields_dict.items():
        f_dx = []
        f_dy = []
        items = fdef.get("items", [])
        for it in items:
            it_name = it.get("name", str(it.get("index", "")))
            bubbles = it.get("bubbles", [])
            for b in bubbles:
                total_bubbles += 1
                exp_x = float(b.get("cx", 0))
                exp_y = float(b.get("cy", 0))
                opt = b.get("option", "")
                b_id = f"{fname}_{it_name}_{opt}" if opt else f"{fname}_{it_name}"

                sr = int(search_radius)
                x1 = max(0, int(round(exp_x - sr)))
                y1 = max(0, int(round(exp_y - sr)))
                x2 = min(w, int(round(exp_x + sr)))
                y2 = min(h, int(round(exp_y + sr)))

                patch = gray_img[y1:y2, x1:x2]
                if patch.size == 0:
                    continue

                thresh = cv2.adaptiveThreshold(
                    patch, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 4
                )
                cnts, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                local_cx = exp_x - x1
                local_cy = exp_y - y1
                best_c = None
                best_dist = float("inf")

                for cnt in cnts:
                    area = cv2.contourArea(cnt)
                    if 45 <= area <= 1400:
                        M = cv2.moments(cnt)
                        if M["m00"] > 0:
                            mcx = M["m10"] / M["m00"]
                            mcy = M["m01"] / M["m00"]
                            dist = float(np.hypot(mcx - local_cx, mcy - local_cy))
                            if dist < best_dist and dist <= (sr * 0.85):
                                best_dist = dist
                                best_c = (float(mcx + x1), float(mcy + y1))

                if best_c is not None:
                    dx = best_c[0] - exp_x
                    dy = best_c[1] - exp_y
                    dist = float(np.hypot(dx, dy))
                    all_dx.append(dx)
                    all_dy.append(dy)
                    f_dx.append(dx)
                    f_dy.append(dy)
                    bubble_deltas.append({
                        "bubble_id": b_id,
                        "field": fname,
                        "expected": (round(exp_x, 1), round(exp_y, 1)),
                        "detected": (round(best_c[0], 1), round(best_c[1], 1)),
                        "dx": round(dx, 2),
                        "dy": round(dy, 2),
                        "dist": round(dist, 2)
                    })

        if f_dx:
            field_summaries[fname] = {
                "median_dx": round(float(np.median(f_dx)), 2),
                "median_dy": round(float(np.median(f_dy)), 2),
                "std_dx": round(float(np.std(f_dx)), 2),
                "std_dy": round(float(np.std(f_dy)), 2),
                "count": len(f_dx)
            }

    if not all_dx:
        return {
            "status": "NO_MATCH",
            "matched_count": 0,
            "total_count": total_bubbles,
            "quality": "FAILED"
        }, []

    med_dx = float(np.median(all_dx))
    med_dy = float(np.median(all_dy))
    std_dx = float(np.std(all_dx))
    std_dy = float(np.std(all_dy))

    is_aligned = (std_dx <= 3.5 and std_dy <= 3.5 and abs(med_dx) <= 3.0 and abs(med_dy) <= 3.0)
    quality = "EXCELLENT" if (std_dx <= 2.5 and std_dy <= 2.5 and abs(med_dx) <= 2.0 and abs(med_dy) <= 2.0) else (
        "ALIGNED" if is_aligned else "NEEDS_CALIBRATION"
    )

    summary = {
        "status": "EVALUATED",
        "quality": quality,
        "matched_count": len(all_dx),
        "total_count": total_bubbles,
        "match_ratio": round(len(all_dx) / max(1, total_bubbles), 3),
        "median_dx": round(med_dx, 2),
        "median_dy": round(med_dy, 2),
        "mean_dx": round(float(np.mean(all_dx)), 2),
        "mean_dy": round(float(np.mean(all_dy)), 2),
        "std_dx": round(std_dx, 2),
        "std_dy": round(std_dy, 2),
        "min_dx": round(float(np.min(all_dx)), 2),
        "max_dx": round(float(np.max(all_dx)), 2),
        "min_dy": round(float(np.min(all_dy)), 2),
        "max_dy": round(float(np.max(all_dy)), 2),
        "by_field": field_summaries
    }
    return summary, bubble_deltas


def draw_alignment_delta_overlay(image, fields_dict, gray_img=None, summary=None, bubble_deltas=None):
    """
    Renders high-visibility diagnostic overlay displaying:
    - Cyan rectangle: Ground truth expected bounding box from JSON
    - Red dot: Expected center
    - Green dot: Detected physical bubble center
    - Yellow line: Delta vector (dx, dy)
    - Top HUD badge summarizing median and std displacement.
    """
    output = image.copy()
    if gray_img is None:
        gray_img = cv2.cvtColor(output, cv2.COLOR_BGR2GRAY) if output.ndim == 3 else output.copy()

    if summary is None or bubble_deltas is None:
        summary, bubble_deltas = evaluate_template_bubble_alignment(gray_img, fields_dict)

    delta_map = {d["bubble_id"]: d for d in bubble_deltas}
    h, w = output.shape[:2]
    scale_factor = max(1.0, min(h, w) / 1200.0)

    # 1. Render per-bubble alignment diagnostics
    for fname, fdef in fields_dict.items():
        items = fdef.get("items", [])
        for it in items:
            it_name = it.get("name", str(it.get("index", "")))
            bubbles = it.get("bubbles", [])
            for b in bubbles:
                opt = b.get("option", "")
                b_id = f"{fname}_{it_name}_{opt}" if opt else f"{fname}_{it_name}"
                cx = int(round(b["cx"]))
                cy = int(round(b["cy"]))
                r = int(round(b.get("radius", 12)))
                shape = b.get("shape", "square")
                bw = int(round(b.get("w", r * 2)))
                bh = int(round(b.get("h", r * 2)))

                # Expected box (Cyan outline)
                x1 = cx - bw // 2
                y1 = cy - bh // 2
                x2 = x1 + bw
                y2 = y1 + bh
                if shape == "square":
                    cv2.rectangle(output, (x1, y1), (x2, y2), (255, 200, 0), 1, cv2.LINE_AA)
                else:
                    cv2.circle(output, (cx, cy), r, (255, 200, 0), 1, cv2.LINE_AA)

                # Expected center (Red dot)
                cv2.circle(output, (cx, cy), 2, (0, 0, 255), -1, cv2.LINE_AA)

                # If detected, draw detected center (Green dot) and delta vector (Yellow line)
                if b_id in delta_map:
                    rec = delta_map[b_id]
                    det_x = int(round(rec["detected"][0]))
                    det_y = int(round(rec["detected"][1]))
                    cv2.circle(output, (det_x, det_y), 2, (0, 255, 0), -1, cv2.LINE_AA)
                    cv2.line(output, (cx, cy), (det_x, det_y), (0, 255, 255), 1, cv2.LINE_AA)

    # 2. Top HUD diagnostic banner
    banner_h = max(36, int(round(44 * scale_factor)))
    cv2.rectangle(output, (0, 0), (w, banner_h), (20, 20, 20), -1)
    cv2.line(output, (0, banner_h), (w, banner_h), (0, 200, 255), 2)

    qual = summary.get("quality", "UNKNOWN")
    med_dx = summary.get("median_dx", 0.0)
    med_dy = summary.get("median_dy", 0.0)
    std_dx = summary.get("std_dx", 0.0)
    std_dy = summary.get("std_dy", 0.0)
    matched = summary.get("matched_count", 0)
    total = summary.get("total_count", 0)

    color_status = (0, 255, 0) if qual in ("EXCELLENT", "ALIGNED") else (0, 140, 255)
    hud_text = (
        f"ALIGNMENT DIAGNOSTIC [{qual}] | "
        f"Delta: dx={med_dx:+.2f}px (std={std_dx:.2f}), dy={med_dy:+.2f}px (std={std_dy:.2f}) | "
        f"Matched: {matched}/{total} bubbles"
    )
    cv2.putText(
        output,
        hud_text,
        (15, int(round(banner_h * 0.65))),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42 * scale_factor,
        color_status,
        1,
        cv2.LINE_AA
    )

    return output
