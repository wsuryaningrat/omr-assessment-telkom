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
        return calculate_fill_ratio(
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
