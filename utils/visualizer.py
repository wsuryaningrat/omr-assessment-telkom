"""
Visualizer module for OMR Debugging.
Generates overlays highlighting registration markers, bubble locations, and detected fills.
"""

from typing import Dict, Any, Optional
import numpy as np
import cv2


def create_omr_debug_overlay(
    aligned_img: np.ndarray,
    result: Dict[str, Any],
    template: Dict[str, Any]
) -> np.ndarray:
    """
    Render high-precision visual debug overlay on top of aligned image:
    - Markers in blue/purple
    - Empty bubble outlines in light gray
    - Detected filled bubbles in green (OK) or red/yellow (ambiguous/multiple)
    - Section borders and header summary banner
    """
    if aligned_img is None:
        return np.zeros((600, 800, 3), dtype=np.uint8)

    overlay = aligned_img.copy()
    if len(overlay.shape) == 2:
        overlay = cv2.cvtColor(overlay, cv2.COLOR_GRAY2BGR)

    h, w = overlay.shape[:2]
    scoring_cfg = template.get("scoring", {})
    fill_thresh = float(scoring_cfg.get("fill_threshold", 0.38))
    blank_thresh = float(scoring_cfg.get("blank_threshold", 0.18))

    # 1. Draw Registration Marker Targets
    marker_cfg = template.get("markers", {})
    target_corners = marker_cfg.get("target_corners", [])
    for idx, (mx, my) in enumerate(target_corners):
        cv2.drawMarker(overlay, (int(mx), int(my)), (255, 0, 128), cv2.MARKER_CROSS, 30, 2)
        cv2.circle(overlay, (int(mx), int(my)), 18, (255, 0, 128), 2)
        cv2.putText(overlay, f"M{idx}", (int(mx) + 15, int(my) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 128), 2)

    # 2. Draw Bubbles
    debug_info = result.get("debug", {})
    all_bubbles = debug_info.get("all_bubbles", [])

    for b in all_bubbles:
        cx, cy = int(b["cx"]), int(b["cy"])
        radius = int(b.get("radius", 10))
        score = float(b.get("score", 0.0))

        if score >= fill_thresh:
            # Marked bubble -> Solid Green circle
            cv2.circle(overlay, (cx, cy), radius, (0, 200, 0), -1)
            cv2.circle(overlay, (cx, cy), radius + 2, (0, 100, 0), 2)
        elif score >= blank_thresh:
            # Weak/Ambiguous mark -> Yellow circle
            cv2.circle(overlay, (cx, cy), radius, (0, 215, 255), 2)
            cv2.circle(overlay, (cx, cy), 3, (0, 215, 255), -1)
        else:
            # Unfilled bubble -> Subtle Cyan/Gray ring
            cv2.circle(overlay, (cx, cy), radius, (200, 200, 200), 1)

    # 3. Draw Summary Banner at Top
    status = result.get("status", "UNKNOWN")
    name = result.get("name", "N/A")
    npm = result.get("npm", "N/A")
    faculty = result.get("faculty", "N/A")
    conf = float(result.get("confidence", 0.0)) * 100

    banner_color = (40, 160, 40) if status == "OK" else ((0, 140, 255) if status == "NEEDS_REVIEW" else (40, 40, 200))
    cv2.rectangle(overlay, (20, 20), (w - 20, 75), banner_color, -1)
    cv2.rectangle(overlay, (20, 20), (w - 20, 75), (255, 255, 255), 2)

    header_text = f"Status: {status} ({conf:.1f}%) | Name: {name} | NPM: {npm} | Fac: {faculty}"
    cv2.putText(overlay, header_text, (35, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

    return overlay
