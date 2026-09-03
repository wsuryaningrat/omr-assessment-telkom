"""
Survey / Self-Assessment Decoding module.
Processes Kuisioner / Self-Assessment questions (A-D choices).
"""

from typing import Dict, List, Tuple, Any
import numpy as np
from .bubbles import score_single_bubble, evaluate_choice_group


def decode_survey(
    inv_gray: np.ndarray,
    binary_img: np.ndarray,
    survey_cfg: Dict[str, Any],
    scoring_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Decode Self-Assessment / Kuisioner questions (choices A, B, C, D).
    """
    total_q = int(survey_cfg.get("total_questions", 10))
    choices = survey_cfg.get("choices", ["A", "B", "C", "D"])
    columns = survey_cfg.get("columns", [])
    radius = int(survey_cfg.get("bubble_radius", 10))

    fill_thresh = float(scoring_cfg.get("fill_threshold", 0.38))
    blank_thresh = float(scoring_cfg.get("blank_threshold", 0.18))
    ambiguity_margin = float(scoring_cfg.get("ambiguity_margin", 0.12))
    inner_ratio = float(scoring_cfg.get("inner_radius_ratio", 0.85))

    answers = {}
    details = {}
    confidences = []
    valid_count = 0
    all_bubble_coords = []

    for col_cfg in columns:
        start_q = int(col_cfg.get("start_q", 1))
        end_q = int(col_cfg.get("end_q", 5))
        start_x = int(col_cfg.get("start_x", 960))
        start_y = int(col_cfg.get("start_y", 960))
        q_spacing_y = int(col_cfg.get("question_spacing_y", 36))
        c_spacing_x = int(col_cfg.get("choice_spacing_x", 26))

        for q_num in range(start_q, end_q + 1):
            if q_num > total_q:
                continue

            q_key = f"S{q_num:02d}"
            q_row = q_num - start_q
            cy = start_y + q_row * q_spacing_y

            choice_scores = []
            for c_idx, choice_lbl in enumerate(choices):
                cx = start_x + c_idx * c_spacing_x
                score_res = score_single_bubble(inv_gray, binary_img, cx, cy, radius, inner_ratio)
                comp_score = score_res["composite_score"]
                choice_scores.append((choice_lbl, comp_score, (cx, cy)))
                all_bubble_coords.append({
                    "field": "survey",
                    "question": q_num,
                    "label": choice_lbl,
                    "cx": cx,
                    "cy": cy,
                    "radius": radius,
                    "score": comp_score
                })

            eval_res = evaluate_choice_group(
                choice_scores,
                fill_threshold=fill_thresh,
                blank_threshold=blank_thresh,
                ambiguity_margin=ambiguity_margin
            )

            val = eval_res["value"]
            status = eval_res["status"]
            conf = eval_res["confidence"]

            answers[q_key] = val
            details[q_key] = {
                "question": q_num,
                "value": val,
                "status": status,
                "confidence": conf,
                "top_two": eval_res["top_two"],
                "scores": eval_res["scores"]
            }

            confidences.append(conf)
            if status == "OK":
                valid_count += 1

    avg_conf = float(np.mean(confidences)) if confidences else 0.0
    overall_status = "OK" if valid_count == total_q else "NEEDS_REVIEW"

    return {
        "answers": answers,
        "details": details,
        "valid_count": valid_count,
        "total_questions": total_q,
        "summary_str": f"{valid_count}/{total_q}",
        "status": overall_status,
        "confidence": round(avg_conf, 3),
        "bubbles": all_bubble_coords
    }
