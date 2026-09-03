"""
Identity Decoding module.
Extracts Nama Lengkap, NPM, and Fakultas from canonical LJK sheet.
"""

from typing import Dict, List, Tuple, Any
import numpy as np
from .bubbles import score_single_bubble, evaluate_choice_group


def decode_name(
    inv_gray: np.ndarray,
    binary_img: np.ndarray,
    name_cfg: Dict[str, Any],
    scoring_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Decode student full name from letter bubble columns (A-Z).
    """
    positions = int(name_cfg.get("positions", 20))
    alphabet = name_cfg.get("alphabet", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    start_x = int(name_cfg.get("start_x", 165))
    start_y = int(name_cfg.get("start_y", 440))
    spacing_x = int(name_cfg.get("spacing_x", 34))
    spacing_y = int(name_cfg.get("spacing_y", 28))
    radius = int(name_cfg.get("bubble_radius", 10))

    fill_thresh = float(scoring_cfg.get("fill_threshold", 0.38))
    blank_thresh = float(scoring_cfg.get("blank_threshold", 0.18))
    ambiguity_margin = float(scoring_cfg.get("ambiguity_margin", 0.12))
    inner_ratio = float(scoring_cfg.get("inner_radius_ratio", 0.85))

    chars = []
    char_details = []
    confidences = []
    is_ambiguous = False
    all_bubble_coords = []

    for col in range(positions):
        choices_scores = []
        cx = start_x + col * spacing_x

        for row, letter in enumerate(alphabet):
            cy = start_y + row * spacing_y
            score_res = score_single_bubble(inv_gray, binary_img, cx, cy, radius, inner_ratio)
            comp_score = score_res["composite_score"]
            choices_scores.append((letter, comp_score, (cx, cy)))
            all_bubble_coords.append({
                "field": "name",
                "col": col,
                "label": letter,
                "cx": cx,
                "cy": cy,
                "radius": radius,
                "score": comp_score
            })

        eval_res = evaluate_choice_group(
            choices_scores,
            fill_threshold=fill_thresh,
            blank_threshold=blank_thresh,
            ambiguity_margin=ambiguity_margin
        )

        char_val = eval_res["value"]
        status = eval_res["status"]
        conf = eval_res["confidence"]

        if status == "BLANK":
            chars.append(" ")
        elif status == "OK":
            chars.append(char_val)
            confidences.append(conf)
        else:
            # Ambiguous or multiple
            chars.append(eval_res["best_choice"] if eval_res["best_choice"] else "?")
            confidences.append(conf)
            is_ambiguous = True

        char_details.append({
            "col": col,
            "char": chars[-1],
            "status": status,
            "confidence": conf,
            "top_two": eval_res["top_two"],
            "scores": eval_res["scores"]
        })

    # Join and trim trailing spaces
    raw_name = "".join(chars)
    decoded_name = raw_name.rstrip()

    # Determine overall status and confidence
    avg_conf = float(np.mean(confidences)) if confidences else 1.0
    if not decoded_name:
        status = "BLANK"
        overall_conf = 0.1
    elif is_ambiguous:
        status = "NEEDS_REVIEW"
        overall_conf = round(min(0.65, avg_conf), 3)
    else:
        status = "OK"
        overall_conf = round(avg_conf, 3)

    return {
        "value": decoded_name,
        "raw_value": raw_name,
        "status": status,
        "confidence": overall_conf,
        "char_details": char_details,
        "bubbles": all_bubble_coords
    }


def decode_npm(
    inv_gray: np.ndarray,
    binary_img: np.ndarray,
    npm_cfg: Dict[str, Any],
    scoring_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Decode 10-digit Student ID (NPM / NIM).
    """
    digits_count = int(npm_cfg.get("digits", 10))
    start_x = int(npm_cfg.get("start_x", 940))
    start_y = int(npm_cfg.get("start_y", 440))
    spacing_x = int(npm_cfg.get("spacing_x", 36))
    spacing_y = int(npm_cfg.get("spacing_y", 30))
    radius = int(npm_cfg.get("bubble_radius", 10))

    fill_thresh = float(scoring_cfg.get("fill_threshold", 0.38))
    blank_thresh = float(scoring_cfg.get("blank_threshold", 0.18))
    ambiguity_margin = float(scoring_cfg.get("ambiguity_margin", 0.12))
    inner_ratio = float(scoring_cfg.get("inner_radius_ratio", 0.85))

    digits = []
    digit_details = []
    confidences = []
    is_ambiguous = False
    all_bubble_coords = []

    for col in range(digits_count):
        choices_scores = []
        cx = start_x + col * spacing_x

        for digit in range(10):
            cy = start_y + digit * spacing_y
            score_res = score_single_bubble(inv_gray, binary_img, cx, cy, radius, inner_ratio)
            comp_score = score_res["composite_score"]
            choices_scores.append((str(digit), comp_score, (cx, cy)))
            all_bubble_coords.append({
                "field": "npm",
                "col": col,
                "label": str(digit),
                "cx": cx,
                "cy": cy,
                "radius": radius,
                "score": comp_score
            })

        eval_res = evaluate_choice_group(
            choices_scores,
            fill_threshold=fill_thresh,
            blank_threshold=blank_thresh,
            ambiguity_margin=ambiguity_margin
        )

        digit_val = eval_res["value"]
        status = eval_res["status"]
        conf = eval_res["confidence"]

        if status == "OK":
            digits.append(digit_val)
            confidences.append(conf)
        elif status == "BLANK":
            digits.append("?")
            confidences.append(conf)
            is_ambiguous = True
        else:
            digits.append(eval_res["best_choice"] if eval_res["best_choice"] else "?")
            confidences.append(conf)
            is_ambiguous = True

        digit_details.append({
            "col": col,
            "digit": digits[-1],
            "status": status,
            "confidence": conf,
            "top_two": eval_res["top_two"],
            "scores": eval_res["scores"]
        })

    decoded_npm = "".join(digits)
    avg_conf = float(np.mean(confidences)) if confidences else 0.0

    if "?" in decoded_npm or is_ambiguous:
        status = "NEEDS_REVIEW"
        overall_conf = round(min(0.65, avg_conf), 3)
    else:
        status = "OK"
        overall_conf = round(avg_conf, 3)

    return {
        "value": decoded_npm,
        "status": status,
        "confidence": overall_conf,
        "digit_details": digit_details,
        "bubbles": all_bubble_coords
    }


def decode_faculty(
    inv_gray: np.ndarray,
    binary_img: np.ndarray,
    faculty_cfg: Dict[str, Any],
    scoring_cfg: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Decode selected Faculty (7 Telkom University faculties).
    """
    options_count = int(faculty_cfg.get("options", 7))
    start_x = int(faculty_cfg.get("start_x", 1360))
    start_y = int(faculty_cfg.get("start_y", 440))
    spacing_y = int(faculty_cfg.get("spacing_y", 36))
    radius = int(faculty_cfg.get("bubble_radius", 10))
    faculty_names = faculty_cfg.get("faculty_names", [
        "FIF (Fakultas Informatika)",
        "FTE (Fakultas Teknik Elektro)",
        "FRI (Fakultas Rekayasa Industri)",
        "FEB (Fakultas Ekonomi dan Bisnis)",
        "FIK (Fakultas Industri Kreatif)",
        "FKS (Fakultas Komunikasi & Ilmu Sosial)",
        "FIT (Fakultas Ilmu Terapan)"
    ])

    fill_thresh = float(scoring_cfg.get("fill_threshold", 0.38))
    blank_thresh = float(scoring_cfg.get("blank_threshold", 0.18))
    ambiguity_margin = float(scoring_cfg.get("ambiguity_margin", 0.12))
    inner_ratio = float(scoring_cfg.get("inner_radius_ratio", 0.85))

    choices_scores = []
    all_bubble_coords = []

    for idx in range(options_count):
        cx = start_x
        cy = start_y + idx * spacing_y
        name = faculty_names[idx] if idx < len(faculty_names) else f"Fac {idx+1}"
        short_code = name.split()[0] if name else f"F{idx+1}"

        score_res = score_single_bubble(inv_gray, binary_img, cx, cy, radius, inner_ratio)
        comp_score = score_res["composite_score"]
        choices_scores.append((short_code, comp_score, (cx, cy)))
        all_bubble_coords.append({
            "field": "faculty",
            "index": idx,
            "label": short_code,
            "full_name": name,
            "cx": cx,
            "cy": cy,
            "radius": radius,
            "score": comp_score
        })

    eval_res = evaluate_choice_group(
        choices_scores,
        fill_threshold=fill_thresh,
        blank_threshold=blank_thresh,
        ambiguity_margin=ambiguity_margin
    )

    faculty_code = eval_res["value"]
    status = eval_res["status"]
    conf = eval_res["confidence"]

    # Match full name
    faculty_full = ""
    for opt in faculty_names:
        if opt.startswith(faculty_code):
            faculty_full = opt
            break

    if not faculty_full and faculty_code:
        faculty_full = faculty_code

    return {
        "value": faculty_code,
        "full_name": faculty_full,
        "status": "OK" if status == "OK" else "NEEDS_REVIEW",
        "confidence": conf,
        "top_two": eval_res["top_two"],
        "scores": eval_res["scores"],
        "bubbles": all_bubble_coords
    }
