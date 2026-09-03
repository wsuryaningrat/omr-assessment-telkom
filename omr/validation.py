"""
Validation and Quality Control module.
Aggregates section confidences, identifies ambiguity, and flags for review.
"""

from typing import Dict, List, Any


def validate_submission(
    alignment_meta: Dict[str, Any],
    name_res: Dict[str, Any],
    npm_res: Dict[str, Any],
    faculty_res: Dict[str, Any],
    survey_res: Dict[str, Any],
    math_res: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Consolidate all section evaluations into a final structured submission verdict.
    Fail-safe rule: If uncertain, flag NEEDS_REVIEW without guessing.
    """
    review_items = []

    # 1. Check Alignment
    if alignment_meta.get("status") != "OK":
        return {
            "status": "FAILED",
            "reason": alignment_meta.get("reason", "ALIGNMENT_FAILED"),
            "confidence": 0.0,
            "review_items": [{"section": "Alignment", "description": alignment_meta.get("reason", "Alignment failed")}],
            "section_confidences": {
                "alignment": 0.0,
                "name": 0.0,
                "npm": 0.0,
                "faculty": 0.0,
                "survey": 0.0,
                "math": 0.0
            }
        }

    # 2. Check Name
    name_conf = name_res.get("confidence", 0.0)
    name_val = name_res.get("value", "")
    if name_res.get("status") != "OK" or not name_val:
        review_items.append({
            "section": "Name",
            "field": "name",
            "description": f"Name reading flagged ({name_res.get('status')}): '{name_val}'",
            "details": name_res.get("char_details", [])
        })

    # 3. Check NPM
    npm_conf = npm_res.get("confidence", 0.0)
    npm_val = npm_res.get("value", "")
    if npm_res.get("status") != "OK" or "?" in npm_val or len(npm_val) < 10:
        review_items.append({
            "section": "NPM",
            "field": "npm",
            "description": f"NPM digit incomplete or ambiguous: '{npm_val}'",
            "details": npm_res.get("digit_details", [])
        })

    # 4. Check Faculty
    faculty_conf = faculty_res.get("confidence", 0.0)
    faculty_val = faculty_res.get("value", "")
    if faculty_res.get("status") != "OK" or not faculty_val:
        review_items.append({
            "section": "Faculty",
            "field": "faculty",
            "description": f"Faculty selection unconfirmed: '{faculty_val}'",
            "details": faculty_res.get("top_two", [])
        })

    # 5. Check Survey
    survey_conf = survey_res.get("confidence", 0.0)
    survey_ambiguous = [q for q, d in survey_res.get("details", {}).items() if d["status"] in ["AMBIGUOUS", "MULTIPLE"]]
    if survey_ambiguous:
        review_items.append({
            "section": "Survey",
            "field": "survey",
            "description": f"Survey questions ambiguous/multiple: {', '.join(survey_ambiguous)}",
            "ambiguous_keys": survey_ambiguous
        })

    # 6. Check Mathematics
    math_conf = math_res.get("confidence", 0.0)
    math_ambiguous = [q for q, d in math_res.get("details", {}).items() if d["status"] in ["AMBIGUOUS", "MULTIPLE"]]
    if math_ambiguous:
        review_items.append({
            "section": "Mathematics",
            "field": "math",
            "description": f"Math questions ambiguous/multiple: {', '.join(math_ambiguous[:10])}{'...' if len(math_ambiguous)>10 else ''}",
            "ambiguous_keys": math_ambiguous
        })

    # Weighted Overall Confidence
    overall_confidence = round(
        0.25 * name_conf +
        0.25 * npm_conf +
        0.15 * faculty_conf +
        0.15 * survey_conf +
        0.20 * math_conf,
        3
    )

    # Determine final status
    if len(review_items) > 0:
        final_status = "NEEDS_REVIEW"
        reason = f"{len(review_items)} field(s) require human review"
    else:
        final_status = "OK"
        reason = "All checks passed deterministically"

    return {
        "status": final_status,
        "reason": reason,
        "confidence": overall_confidence,
        "review_items": review_items,
        "section_confidences": {
            "name": name_conf,
            "npm": npm_conf,
            "faculty": faculty_conf,
            "survey": survey_conf,
            "math": math_conf
        }
    }
