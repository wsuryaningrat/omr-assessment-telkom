_ANCHOR_CACHE = {}

def get_answer_grid_anchor(gray_img):
    img_id = id(gray_img)
    if img_id in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[img_id]
    h_img, w_img = gray_img.shape[:2]
    crop = gray_img[1640:min(h_img, 1840), 150:320]
    _, th = cv2.threshold(crop, 120, 255, cv2.THRESH_BINARY_INV)
    v_lines = cv2.morphologyEx(th, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 12)))

    q1_top = 1675.0
    for i in range(len(v_lines)):
        row = v_lines[i]
        peaks = [x for x in range(1, len(row) - 1) if row[x] > 0 and row[x - 1] == 0]
        if len(peaks) >= 4:
            found_top = float(1640 + i)
            if abs(found_top - 1675.0) > 18.0:
                q1_top = found_top
            break

    if abs(q1_top - 1675.0) > 18.0:
        bot_crop = gray_img[2350:min(h_img, 2400), 140:1600]
        _, th_bot = cv2.threshold(bot_crop, 120, 255, cv2.THRESH_BINARY_INV)
        bot_proj = np.sum(th_bot, axis=1) / 255.0
        bot_peaks = [2350 + i for i in range(1, len(bot_proj) - 1) if bot_proj[i] > 250]
        q_bot = float(bot_peaks[-1]) if bot_peaks else 2380.0
        sy = (q_bot - q1_top) / max(1.0, 2380.0 - 1675.0)
    else:
        q1_top = 1675.0
        sy = 1.0

    res = (q1_top, sy)
    _ANCHOR_CACHE[img_id] = res
    return res

import cv2
import numpy as np
from core.detector import calculate_fill_ratio, evaluate_question

def decode_field(gray_img, field_def, thresh=0.28, margin=0.08):
    """
    Decodes a field based on its template definition and detected bubbles (circles or squares).
    Accurately handles both pencil shading and pen 'X' cross marks.
    Eliminates false-positive detections on dense letters like 'W', 'M', 'B', 'D'.
    """
    field_name = field_def.get("field_name", "field")
    field_type = field_def.get("field_type", "multiple_choice")
    items = field_def.get("items", [])

    # Global paper baseline reference for this scanned page
    paper_bg = float(np.percentile(gray_img, 92))
    if paper_bg < 150:
        paper_bg = 240.0

    decoded_values = {}

    def get_bubble_ratio(b):
        cy = b["cy"]
        if field_name.startswith("Soal-"):
            q1_top, sy = get_answer_grid_anchor(gray_img)
            if abs(q1_top - 1675.0) > 18.0:
                cy = q1_top + (b["cy"] - 1675.0) * sy
        return calculate_fill_ratio(
            gray_img,
            b["cx"],
            cy,
            b.get("radius", 12),
            shape=b.get("shape", "square"),
            w=b.get("w"),
            h=b.get("h"),
            paper_bg=paper_bg,
            option_glyph=b.get("option")
        )

    if field_type == "text":
        chars = []
        for it in items:
            bubbles = it.get("bubbles", [])
            ratios = [get_bubble_ratio(b) for b in bubbles]
            idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)

            if status == "OK" and idx >= 0 and idx < len(bubbles):
                chars.append(bubbles[idx].get("option", chr(65 + idx)))
            elif status == "BLANK":
                chars.append(" ")
            else:
                chars.append("?") # MULTIPLE
        decoded_str = "".join(chars).rstrip()
        decoded_values[field_name] = decoded_str

    elif field_type == "number":
        digits = []
        for it in items:
            bubbles = it.get("bubbles", [])
            ratios = [get_bubble_ratio(b) for b in bubbles]
            idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)

            if status == "OK" and idx >= 0 and idx < len(bubbles):
                digits.append(str(bubbles[idx].get("option", idx)))
            elif status == "BLANK":
                digits.append(" ")
            else:
                digits.append("?")
        decoded_str = "".join(digits).rstrip()
        decoded_values[field_name] = decoded_str

    elif field_type == "choice":
        all_choices = []
        for it in items:
            bubbles = it.get("bubbles", [])
            ratios = [get_bubble_ratio(b) for b in bubbles]
            idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)
            if status == "OK" and idx >= 0 and idx < len(bubbles):
                all_choices.append(bubbles[idx].get("option", f"Opt_{idx+1}"))
            elif status == "BLANK":
                all_choices.append("BLANK")
            else:
                all_choices.append(status)
        decoded_values[field_name] = all_choices[0] if len(all_choices) == 1 else ", ".join(all_choices)

    else:
        # multiple_choice (e.g. Kuesioner 1-15, Soal 1-75)
        for it in items:
            item_name = it.get("name", f"Item_{it.get('index', 1)}")
            bubbles = it.get("bubbles", [])
            ratios = [get_bubble_ratio(b) for b in bubbles]
            idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)

            if status == "OK" and idx >= 0 and idx < len(bubbles):
                ans = bubbles[idx].get("option", chr(65 + idx))
            elif status == "BLANK":
                ans = "BLANK"
            else:
                ans = status # MULTIPLE

            decoded_values[item_name] = ans

    return decoded_values


def decode_field_detailed(gray_img, field_def, thresh=0.28, margin=0.08):
    """
    Extended decoder returning both decoded field values and per-bubble confidence / fill ratios.
    """
    field_name = field_def.get("field_name", "field")
    field_type = field_def.get("field_type", "multiple_choice")
    items = field_def.get("items", [])

    paper_bg = float(np.percentile(gray_img, 92))
    if paper_bg < 150:
        paper_bg = 240.0

    decoded_values = {}
    analysis = {}

    def get_bubble_ratio(b):
        cy = b["cy"]
        if field_name.startswith("Soal-"):
            q1_top, sy = get_answer_grid_anchor(gray_img)
            if abs(q1_top - 1675.0) > 18.0:
                cy = q1_top + (b["cy"] - 1675.0) * sy
        return calculate_fill_ratio(
            gray_img,
            b["cx"],
            cy,
            b.get("radius", 12),
            shape=b.get("shape", "square"),
            w=b.get("w"),
            h=b.get("h"),
            paper_bg=paper_bg,
            option_glyph=b.get("option")
        )

    if field_type == "text":
        chars = []
        for it in items:
            bubbles = it.get("bubbles", [])
            ratios = [get_bubble_ratio(b) for b in bubbles]
            idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)
            if status == "OK" and 0 <= idx < len(bubbles):
                chars.append(bubbles[idx].get("option", chr(65 + idx)))
            elif status == "BLANK":
                chars.append(" ")
            else:
                chars.append("?")
        decoded_str = "".join(chars).rstrip()
        decoded_values[field_name] = decoded_str

    elif field_type == "number":
        digits = []
        for it in items:
            bubbles = it.get("bubbles", [])
            ratios = [get_bubble_ratio(b) for b in bubbles]
            idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)
            if status == "OK" and 0 <= idx < len(bubbles):
                digits.append(str(bubbles[idx].get("option", idx)))
            elif status == "BLANK":
                digits.append(" ")
            else:
                digits.append("?")
        decoded_str = "".join(digits).rstrip()
        decoded_values[field_name] = decoded_str

    elif field_type == "choice":
        all_choices = []
        for it in items:
            bubbles = it.get("bubbles", [])
            ratios = [get_bubble_ratio(b) for b in bubbles]
            idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)
            if status == "OK" and 0 <= idx < len(bubbles):
                all_choices.append(bubbles[idx].get("option", f"Opt_{idx+1}"))
            elif status == "BLANK":
                all_choices.append("BLANK")
            else:
                all_choices.append(status)
        decoded_values[field_name] = all_choices[0] if len(all_choices) == 1 else ", ".join(all_choices)

    else:
        for it in items:
            item_name = it.get("name", f"Item_{it.get('index', 1)}")
            bubbles = it.get("bubbles", [])
            ratios = [get_bubble_ratio(b) for b in bubbles]
            idx, status = evaluate_question(ratios, threshold=thresh, ambiguous_margin=margin)

            if status == "OK" and 0 <= idx < len(bubbles):
                ans = bubbles[idx].get("option", chr(65 + idx))
                conf = round(float(ratios[idx]) * 100, 1)
            elif status == "BLANK":
                ans = "BLANK"
                conf = 0.0
            else:
                ans = status
                conf = round(float(max(ratios)) * 100, 1) if ratios else 0.0

            decoded_values[item_name] = ans
            analysis[item_name] = {
                "ai_prediction": ans,
                "confidence": conf,
                "status": status,
                "ratios": {b.get("option", chr(65 + i)): round(float(r), 3) for i, (b, r) in enumerate(zip(bubbles, ratios))}
            }

    return decoded_values, analysis
