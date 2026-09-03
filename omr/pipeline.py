"""
Main Pipeline module.
High-level API for end-to-end OMR processing: process_ljk(image, template)
"""

from typing import Dict, Any, Optional
import numpy as np
import cv2

from .alignment import align_image
from .preprocessing import preprocess_aligned_image
from .identity import decode_name, decode_npm, decode_faculty
from .survey import decode_survey
from .mathematics import decode_mathematics
from .validation import validate_submission


def process_ljk(
    image: np.ndarray,
    template: Dict[str, Any],
    filename: str = "unknown.jpg"
) -> Dict[str, Any]:
    """
    100% Deterministic OpenCV OMR Pipeline.

    Pipeline:
    Image -> Marker Detection -> Quality Gate -> Perspective Correction ->
    Preprocessing -> Bubble Scoring -> Decode Identity -> Decode Survey ->
    Decode Math 1-100 -> Validation -> Structured Result
    """
    if image is None:
        return {
            "filename": filename,
            "name": "",
            "npm": "",
            "faculty": "",
            "faculty_full": "",
            "survey": {"answers": {}, "summary": "0/10", "valid_count": 0},
            "math": {"answers": {}, "summary": "0/100", "valid_count": 0, "blank_count": 100, "ambiguous_count": 0},
            "status": "FAILED",
            "confidence": 0.0,
            "reason": "EMPTY_IMAGE",
            "aligned_image": None
        }

    # Step 1: Alignment & Quality Gate
    aligned_img, align_meta = align_image(image, template)

    if aligned_img is None or align_meta.get("status") != "OK":
        return {
            "filename": filename,
            "name": "",
            "npm": "",
            "faculty": "",
            "faculty_full": "",
            "survey": {"answers": {}, "summary": "0/10", "valid_count": 0},
            "math": {"answers": {}, "summary": "0/100", "valid_count": 0, "blank_count": 100, "ambiguous_count": 0},
            "status": "FAILED",
            "confidence": 0.0,
            "reason": align_meta.get("reason", "ALIGNMENT_FAILED"),
            "alignment": align_meta,
            "aligned_image": None
        }

    # Step 2: Deterministic Preprocessing
    preprocessed = preprocess_aligned_image(aligned_img)
    inv_gray = preprocessed["inv_gray"]
    binary_combined = preprocessed["binary_combined"]

    scoring_cfg = template.get("scoring", {})
    identity_cfg = template.get("identity", {})
    name_cfg = identity_cfg.get("name", {})
    npm_cfg = identity_cfg.get("npm", {})
    faculty_cfg = identity_cfg.get("faculty", {})
    survey_cfg = template.get("survey", {})
    math_cfg = template.get("mathematics", {})

    # Step 3: Decode Identity Sections
    name_res = decode_name(inv_gray, binary_combined, name_cfg, scoring_cfg)
    npm_res = decode_npm(inv_gray, binary_combined, npm_cfg, scoring_cfg)
    faculty_res = decode_faculty(inv_gray, binary_combined, faculty_cfg, scoring_cfg)

    # Step 4: Decode Survey
    survey_res = decode_survey(inv_gray, binary_combined, survey_cfg, scoring_cfg)

    # Step 5: Decode Mathematics (1-100)
    math_res = decode_mathematics(inv_gray, binary_combined, math_cfg, scoring_cfg)

    # Step 6: Consolidate & Validate Verdict
    verdict = validate_submission(
        align_meta,
        name_res,
        npm_res,
        faculty_res,
        survey_res,
        math_res
    )

    # Combine all bubble coordinates for visual debug overlay
    all_bubbles = (
        name_res.get("bubbles", []) +
        npm_res.get("bubbles", []) +
        faculty_res.get("bubbles", []) +
        survey_res.get("bubbles", []) +
        math_res.get("bubbles", [])
    )

    return {
        "filename": filename,
        "name": name_res.get("value", ""),
        "npm": npm_res.get("value", ""),
        "faculty": faculty_res.get("value", ""),
        "faculty_full": faculty_res.get("full_name", ""),
        "survey": {
            "answers": survey_res.get("answers", {}),
            "summary": survey_res.get("summary_str", "0/10"),
            "valid_count": survey_res.get("valid_count", 0),
            "status": survey_res.get("status", "NEEDS_REVIEW")
        },
        "math": {
            "answers": math_res.get("answers", {}),
            "summary": math_res.get("summary_str", "0/100"),
            "valid_count": math_res.get("valid_count", 0),
            "correct_count": math_res.get("correct_count", 0),
            "wrong_count": math_res.get("wrong_count", 0),
            "blank_count": math_res.get("blank_count", 0),
            "ambiguous_count": math_res.get("ambiguous_count", 0),
            "score": math_res.get("score", 0.0),
            "status": math_res.get("status", "NEEDS_REVIEW")
        },
        "score": math_res.get("score", 0.0),
        "status": verdict.get("status", "NEEDS_REVIEW"),
        "confidence": verdict.get("confidence", 0.0),
        "reason": verdict.get("reason", ""),
        "section_confidences": verdict.get("section_confidences", {}),
        "review_items": verdict.get("review_items", []),
        "alignment": align_meta,
        "aligned_image": aligned_img,
        "debug": {
            "name_details": name_res,
            "npm_details": npm_res,
            "faculty_details": faculty_res,
            "survey_details": survey_res,
            "math_details": math_res,
            "all_bubbles": all_bubbles
        }
    }
