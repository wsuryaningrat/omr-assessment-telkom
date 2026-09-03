"""
Mathematics Pretest Decoding module.
Processes 100 multiple choice questions (A-D) arranged across grid columns.
"""

from typing import Dict, List, Tuple, Any
import numpy as np
from .bubbles import score_single_bubble, evaluate_choice_group


def decode_mathematics(
    inv_gray: np.ndarray,
    binary_img: np.ndarray,
    math_cfg: Dict[str, Any],
    scoring_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Decode 100 Mathematics Pretest questions (choices A, B, C, D).
    """
    total_q = int(math_cfg.get("total_questions", 100))
    choices = math_cfg.get("choices", ["A", "B", "C", "D"])
    columns = math_cfg.get("columns", [])
    radius = int(math_cfg.get("bubble_radius", 10))

    fill_thresh = float(scoring_cfg.get("fill_threshold", 0.38))
    blank_thresh = float(scoring_cfg.get("blank_threshold", 0.18))
    ambiguity_margin = float(scoring_cfg.get("ambiguity_margin", 0.12))
    inner_ratio = float(scoring_cfg.get("inner_radius_ratio", 0.85))

    answer_key = math_cfg.get("answer_key", {})
    answers = {}
    details = {}
    confidences = []
    valid_count = 0
    correct_count = 0
    wrong_count = 0
    blank_count = 0
    ambiguous_count = 0
    all_bubble_coords = []

    for col_cfg in columns:
        start_q = int(col_cfg.get("start_q", 1))
        end_q = int(col_cfg.get("end_q", 25))
        start_x = int(col_cfg.get("start_x", 180))
        start_y = int(col_cfg.get("start_y", 1360))
        q_spacing_y = int(col_cfg.get("question_spacing_y", 35))
        c_spacing_x = int(col_cfg.get("choice_spacing_x", 26))

        for q_num in range(start_q, end_q + 1):
            if q_num > total_q:
                continue

            q_key = f"Q{q_num:02d}" if q_num < 100 else "Q100"
            q_row = q_num - start_q
            cy = start_y + q_row * q_spacing_y

            choice_scores = []
            for c_idx, choice_lbl in enumerate(choices):
                cx = start_x + c_idx * c_spacing_x
                score_res = score_single_bubble(inv_gray, binary_img, cx, cy, radius, inner_ratio)
                comp_score = score_res["composite_score"]
                choice_scores.append((choice_lbl, comp_score, (cx, cy)))
                all_bubble_coords.append({
                    "field": "math",
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

            expected = answer_key.get(q_key, "")
            is_correct = (val == expected) if (expected and val in ["A", "B", "C", "D"]) else False

            answers[q_key] = val
            details[q_key] = {
                "question": q_num,
                "value": val,
                "expected": expected,
                "is_correct": is_correct,
                "status": status,
                "confidence": conf,
                "top_two": eval_res["top_two"],
                "scores": eval_res["scores"]
            }

            confidences.append(conf)
            if status == "OK":
                valid_count += 1
                if answer_key and expected:
                    if is_correct:
                        correct_count += 1
                    else:
                        wrong_count += 1
            elif status == "BLANK":
                blank_count += 1
            else:
                ambiguous_count += 1

    avg_conf = float(np.mean(confidences)) if confidences else 0.0
    overall_status = "OK" if ambiguous_count == 0 else "NEEDS_REVIEW"
    score = round((correct_count / total_q) * 100, 1) if (answer_key and total_q > 0) else 0.0

    return {
        "answers": answers,
        "details": details,
        "valid_count": valid_count,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "blank_count": blank_count,
        "ambiguous_count": ambiguous_count,
        "total_questions": total_q,
        "score": score,
        "summary_str": f"{valid_count}/{total_q}",
        "status": overall_status,
        "confidence": round(avg_conf, 3),
        "bubbles": all_bubble_coords
    }

