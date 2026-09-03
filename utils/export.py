"""
Export module for generating CSV and multi-sheet Excel reports.
"""

from typing import List, Dict, Any
import io
import pandas as pd
from datetime import datetime


def prepare_dataframe(results: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Transform raw list of structured submission results into a standardized pandas DataFrame.
    """
    rows = []
    for r in results:
        row = {
            "Filename": r.get("filename", ""),
            "Name": r.get("name", ""),
            "NPM": r.get("npm", ""),
            "Faculty": r.get("faculty", ""),
            "Faculty_Full": r.get("faculty_full", ""),
            "Status": r.get("status", ""),
            "Confidence": round(float(r.get("confidence", 0.0)) * 100, 1),
            "Survey_Summary": r.get("survey", {}).get("summary", "0/10"),
            "Math_Summary": r.get("math", {}).get("summary", "0/100"),
            "Math_Score": r.get("math", {}).get("score", 0.0),
            "Math_Correct_Count": r.get("math", {}).get("correct_count", 0),
            "Math_Wrong_Count": r.get("math", {}).get("wrong_count", 0),
            "Math_Blank_Count": r.get("math", {}).get("blank_count", 0),
            "Math_Ambiguous_Count": r.get("math", {}).get("ambiguous_count", 0),
            "Reason": r.get("reason", "")
        }

        # Unpack survey answers: survey_01 ... survey_10
        survey_answers = r.get("survey", {}).get("answers", {})
        for i in range(1, 11):
            k = f"S{i:02d}"
            row[f"survey_{i:02d}"] = survey_answers.get(k, "")

        # Unpack math answers: math_01 ... math_100
        math_answers = r.get("math", {}).get("answers", {})
        for i in range(1, 101):
            k = f"Q{i:02d}" if i < 100 else "Q100"
            row[f"math_{i:02d}"] = math_answers.get(k, "")

        rows.append(row)

    return pd.DataFrame(rows)


def export_to_csv(results: List[Dict[str, Any]]) -> str:
    """Export results to CSV formatted string."""
    df = prepare_dataframe(results)
    return df.to_csv(index=False)


def export_to_excel(results: List[Dict[str, Any]]) -> bytes:
    """
    Export results to multi-sheet Excel binary with sheets:
    - Results: Main table
    - Review: Flagged submissions
    - Processing Log: Technical metadata
    """
    df_results = prepare_dataframe(results)

    # Sheet 2: Review (items with NEEDS_REVIEW or FAILED)
    df_review = df_results[df_results["Status"].isin(["NEEDS_REVIEW", "FAILED", "AMBIGUOUS"])].copy()

    # Sheet 3: Processing Log
    log_rows = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in results:
        align = r.get("alignment", {})
        sec_conf = r.get("section_confidences", {})
        log_rows.append({
            "Timestamp": now_str,
            "Filename": r.get("filename", ""),
            "Status": r.get("status", ""),
            "Overall_Confidence": r.get("confidence", 0.0),
            "Alignment_Status": align.get("status", "N/A"),
            "Alignment_Method": align.get("method", "N/A"),
            "Blur_Score": align.get("blur_score", 0.0),
            "Conf_Name": sec_conf.get("name", 0.0),
            "Conf_NPM": sec_conf.get("npm", 0.0),
            "Conf_Faculty": sec_conf.get("faculty", 0.0),
            "Conf_Survey": sec_conf.get("survey", 0.0),
            "Conf_Math": sec_conf.get("math", 0.0),
            "Reason": r.get("reason", "")
        })
    df_log = pd.DataFrame(log_rows)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_results.to_excel(writer, sheet_name="Results", index=False)
        df_review.to_excel(writer, sheet_name="Review", index=False)
        df_log.to_excel(writer, sheet_name="Processing Log", index=False)

    return output.getvalue()
