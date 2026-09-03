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


def draw_reading_overlay(image, fields_dict, gray_img, thresh=0.28):
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
            marked_idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=0.08)

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
