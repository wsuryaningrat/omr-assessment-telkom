"""
Bubble extraction and deterministic scoring module.
Measures fill ratios and intensities using inner circular masks.
"""

from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import cv2


def score_single_bubble(
    inv_gray: np.ndarray,
    binary_img: np.ndarray,
    center_x: int,
    center_y: int,
    radius: int,
    inner_ratio: float = 0.85
) -> Dict[str, float]:
    """
    Score a single circular bubble region deterministically.
    Uses an inner circular mask to avoid printed border artifacts.
    """
    h, w = inv_gray.shape[:2]
    inner_r = max(2, int(radius * inner_ratio))

    # Bounding box for bubble mask
    x1 = max(0, center_x - inner_r)
    y1 = max(0, center_y - inner_r)
    x2 = min(w, center_x + inner_r + 1)
    y2 = min(h, center_y + inner_r + 1)

    if x2 <= x1 or y2 <= y1:
        return {"fill_ratio_bin": 0.0, "fill_ratio_gray": 0.0, "composite_score": 0.0}

    # Create local circular mask
    mask_h = y2 - y1
    mask_w = x2 - x1
    local_mask = np.zeros((mask_h, mask_w), dtype=np.uint8)
    cv2.circle(local_mask, (center_x - x1, center_y - y1), inner_r, 255, -1)

    # Extract ROI
    roi_inv_gray = inv_gray[y1:y2, x1:x2]
    roi_binary = binary_img[y1:y2, x1:x2]

    # Mask pixel count
    mask_pixel_count = int(np.count_nonzero(local_mask))
    if mask_pixel_count == 0:
        return {"fill_ratio_bin": 0.0, "fill_ratio_gray": 0.0, "composite_score": 0.0}

    # Binary fill ratio: fraction of dark pixels in inner circle
    bin_dark_pixels = int(np.count_nonzero(cv2.bitwise_and(roi_binary, roi_binary, mask=local_mask)))
    fill_ratio_bin = float(bin_dark_pixels) / mask_pixel_count

    # Grayscale inverted mean: intensity of shading inside inner circle
    gray_mean = float(cv2.mean(roi_inv_gray, mask=local_mask)[0])
    fill_ratio_gray = gray_mean / 255.0

    # Composite score (weighted blend)
    composite_score = 0.65 * fill_ratio_bin + 0.35 * fill_ratio_gray

    return {
        "fill_ratio_bin": round(fill_ratio_bin, 4),
        "fill_ratio_gray": round(fill_ratio_gray, 4),
        "composite_score": round(composite_score, 4)
    }


def evaluate_choice_group(
    choices_scores: List[Tuple[str, float, Tuple[int, int]]],
    fill_threshold: float = 0.38,
    blank_threshold: float = 0.18,
    ambiguity_margin: float = 0.12
) -> Dict[str, Any]:
    """
    Evaluate a group of choices (e.g. A, B, C, D) for a question or character column.
    choices_scores: list of (choice_label, score, (cx, cy))
    """
    if not choices_scores:
        return {
            "value": "",
            "status": "BLANK",
            "confidence": 0.0,
            "best_choice": "",
            "scores": {},
            "top_two": []
        }

    # Sort choices by score descending
    sorted_choices = sorted(choices_scores, key=lambda x: x[1], reverse=True)
    scores_dict = {lbl: score for lbl, score, _ in choices_scores}

    best_label, best_score, best_coord = sorted_choices[0]
    second_label, second_score, second_coord = sorted_choices[1] if len(sorted_choices) > 1 else ("", 0.0, (0, 0))

    top_two = [
        {"choice": best_label, "score": best_score, "coord": best_coord},
        {"choice": second_label, "score": second_score, "coord": second_coord}
    ]

    # Case 1: All blank (below blank threshold)
    if best_score < blank_threshold:
        return {
            "value": "",
            "status": "BLANK",
            "confidence": round(max(0.1, 1.0 - best_score * 2.0), 3),
            "best_choice": "",
            "scores": scores_dict,
            "top_two": top_two
        }

    # Case 2: Multiple marked bubbles (both above fill threshold and close in score)
    if best_score >= fill_threshold and second_score >= fill_threshold and (best_score - second_score) < ambiguity_margin:
        return {
            "value": f"{best_label}+{second_label}",
            "status": "MULTIPLE",
            "confidence": round(0.40, 3),
            "best_choice": best_label,
            "scores": scores_dict,
            "top_two": top_two
        }

    # Case 3: Clear single answer (above fill threshold with sufficient margin)
    if best_score >= fill_threshold and (best_score - second_score) >= ambiguity_margin:
        diff_ratio = (best_score - second_score) / max(0.01, best_score)
        conf = min(1.0, 0.60 + 0.40 * diff_ratio)
        return {
            "value": best_label,
            "status": "OK",
            "confidence": round(conf, 3),
            "best_choice": best_label,
            "scores": scores_dict,
            "top_two": top_two
        }

    # Case 4: Ambiguous mark (intermediate fill, faint pencil, or smudge)
    diff = best_score - second_score
    conf = min(0.65, max(0.20, best_score * 0.8 + diff * 0.2))
    return {
        "value": best_label,
        "status": "AMBIGUOUS",
        "confidence": round(conf, 3),
        "best_choice": best_label,
        "scores": scores_dict,
        "top_two": top_two
    }
