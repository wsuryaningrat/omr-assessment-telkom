"""
Synthetic LJK Generator for automated testing and calibration verification.
Renders canonical and simulated camera photos with ArUco/fiducial markers and student marks.
"""

from typing import Dict, List, Optional, Any
import numpy as np
import cv2


def create_blank_canonical_ljk(template: Dict[str, Any]) -> np.ndarray:
    """
    Render a clean blank canonical LJK sheet based on template configuration.
    """
    w = int(template.get("canonical_width", 1654))
    h = int(template.get("canonical_height", 2339))

    # White page background
    img = np.full((h, w, 3), 255, dtype=np.uint8)

    # 4 Corner ArUco Markers with white quiet zone
    marker_cfg = template.get("markers", {})
    target_corners = marker_cfg.get("target_corners", [[60, 60], [w - 60, 60], [w - 60, h - 60], [60, h - 60]])
    marker_size = int(marker_cfg.get("marker_size", 60))
    dict_name = marker_cfg.get("aruco_dict", "DICT_4X4_50")
    marker_ids = marker_cfg.get("marker_ids", [0, 1, 2, 3])

    try:
        dict_id = getattr(cv2.aruco, dict_name, cv2.aruco.DICT_4X4_50)
        aruco_dict = cv2.aruco.getPredefinedDictionary(dict_id)
        for idx, (cx, cy) in enumerate(target_corners):
            m_id = marker_ids[idx] if idx < len(marker_ids) else idx
            # Generate ArUco marker
            m_inner = marker_size - 10
            marker_img = np.zeros((m_inner, m_inner), dtype=np.uint8)
            if hasattr(cv2.aruco, "drawMarker"):
                cv2.aruco.drawMarker(aruco_dict, m_id, m_inner, marker_img, 1)
            elif hasattr(cv2.aruco, "generateImageMarker"):
                cv2.aruco.generateImageMarker(aruco_dict, m_id, m_inner, marker_img, 1)
            
            # White quiet zone
            marker_padded = cv2.copyMakeBorder(marker_img, 5, 5, 5, 5, cv2.BORDER_CONSTANT, value=255)
            marker_bgr = cv2.cvtColor(marker_padded, cv2.COLOR_GRAY2BGR)

            x1 = int(cx - marker_size // 2)
            y1 = int(cy - marker_size // 2)
            img[y1:y1 + marker_size, x1:x1 + marker_size] = marker_bgr
    except Exception:
        # Fallback: Solid black corner boxes
        for cx, cy in target_corners:
            x1 = int(cx - marker_size // 2)
            y1 = int(cy - marker_size // 2)
            cv2.rectangle(img, (x1, y1), (x1 + marker_size, y1 + marker_size), (0, 0, 0), -1)

    # Header Titles
    cv2.putText(img, "LEMBAR JAWABAN KOMPUTER", (140, 120), cv2.FONT_HERSHEY_DUPLEX, 1.1, (0, 0, 0), 2)
    cv2.putText(img, "MATH PROFICIENCY TEST - TELKOM UNIVERSITY", (140, 160), cv2.FONT_HERSHEY_DUPLEX, 0.8, (186, 12, 47), 2)
    cv2.putText(img, "MATH CLINIC CENTER", (140, 195), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 80), 2)

    # Petunjuk Box
    cv2.rectangle(img, (900, 80), (w - 100, 240), (0, 0, 0), 1)
    cv2.putText(img, "PETUNJUK:", (920, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    cv2.putText(img, "1. Gunakan pensil 2B / pulpen hitam.", (920, 135), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (40, 40, 40), 1)
    cv2.putText(img, "2. Hitamkan bulatan penuh pada jawaban yang benar.", (920, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (40, 40, 40), 1)
    cv2.putText(img, "3. Bersihkan arsiran bila ingin mengganti jawaban.", (920, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (40, 40, 40), 1)

    # 1. Section: Nama Lengkap
    name_cfg = template["identity"]["name"]
    n_pos = name_cfg["positions"]
    n_alpha = name_cfg["alphabet"]
    n_sx = name_cfg["start_x"]
    n_sy = name_cfg["start_y"]
    n_spx = name_cfg["spacing_x"]
    n_spy = name_cfg["spacing_y"]
    n_rad = name_cfg["bubble_radius"]

    cv2.rectangle(img, (n_sx - 30, n_sy - 90), (n_sx + n_pos * n_spx + 10, n_sy + len(n_alpha) * n_spy + 15), (0, 0, 0), 2)
    cv2.putText(img, "Nama Lengkap", (n_sx + 200, n_sy - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

    # Name write-in boxes
    for col in range(n_pos):
        bx = n_sx + col * n_spx
        cv2.rectangle(img, (bx - n_rad - 2, n_sy - 45), (bx + n_rad + 2, n_sy - 15), (0, 0, 0), 1)

    # Name letter bubbles (thin clean rings with small centered letter)
    for col in range(n_pos):
        bx = n_sx + col * n_spx
        for row, letter in enumerate(n_alpha):
            by = n_sy + row * n_spy
            cv2.circle(img, (bx, by), n_rad, (120, 120, 120), 1)
            cv2.putText(img, letter, (bx - 4, by + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.28, (120, 120, 120), 1)

    # 2. Section: NPM
    npm_cfg = template["identity"]["npm"]
    m_dig = npm_cfg["digits"]
    m_sx = npm_cfg["start_x"]
    m_sy = npm_cfg["start_y"]
    m_spx = npm_cfg["spacing_x"]
    m_spy = npm_cfg["spacing_y"]
    m_rad = npm_cfg["bubble_radius"]

    cv2.rectangle(img, (m_sx - 20, m_sy - 90), (m_sx + m_dig * m_spx + 10, m_sy + 10 * m_spy + 15), (0, 0, 0), 2)
    cv2.putText(img, "NPM", (m_sx + 120, m_sy - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

    # NPM write-in boxes
    for col in range(m_dig):
        bx = m_sx + col * m_spx
        cv2.rectangle(img, (bx - m_rad - 2, m_sy - 45), (bx + m_rad + 2, m_sy - 15), (0, 0, 0), 1)

    # NPM digit bubbles (0-9)
    for col in range(m_dig):
        bx = m_sx + col * m_spx
        for d in range(10):
            by = m_sy + d * m_spy
            cv2.circle(img, (bx, by), m_rad, (120, 120, 120), 1)
            cv2.putText(img, str(d), (bx - 3, by + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.30, (120, 120, 120), 1)

    # 3. Section: Fakultas
    fac_cfg = template["identity"]["faculty"]
    f_opt = fac_cfg["options"]
    f_sx = fac_cfg["start_x"]
    f_sy = fac_cfg["start_y"]
    f_spy = fac_cfg["spacing_y"]
    f_rad = fac_cfg["bubble_radius"]
    f_names = fac_cfg["faculty_names"]

    cv2.rectangle(img, (f_sx - 20, f_sy - 90), (w - 60, f_sy + f_opt * f_spy + 15), (0, 0, 0), 2)
    cv2.putText(img, "Fakultas", (f_sx + 60, f_sy - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

    for idx in range(f_opt):
        by = f_sy + idx * f_spy
        cv2.circle(img, (f_sx, by), f_rad, (120, 120, 120), 1)
        name = f_names[idx] if idx < len(f_names) else f"Fakultas {idx+1}"
        cv2.putText(img, name, (f_sx + 20, by + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 0, 0), 1)

    # 4. Section: Kuisioner / Self-Assessment
    surv_cfg = template["survey"]
    cv2.rectangle(img, (900, 890), (w - 60, 1200), (0, 0, 0), 2)
    cv2.putText(img, "Kuisioner (Self-Assessment)", (920, 930), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 2)

    for col_cfg in surv_cfg["columns"]:
        s_q = col_cfg["start_q"]
        e_q = col_cfg["end_q"]
        sx = col_cfg["start_x"]
        sy = col_cfg["start_y"]
        q_spy = col_cfg["question_spacing_y"]
        c_spx = col_cfg["choice_spacing_x"]

        for q in range(s_q, e_q + 1):
            row = q - s_q
            qy = sy + row * q_spy
            cv2.putText(img, f"{q:02d}", (sx - 35, qy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
            for c_idx, choice in enumerate(surv_cfg["choices"]):
                cx = sx + c_idx * c_spx
                cv2.circle(img, (cx, qy), surv_cfg["bubble_radius"], (120, 120, 120), 1)
                cv2.putText(img, choice, (cx - 3, qy + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.28, (120, 120, 120), 1)

    # 5. Section: Matematika 1-100
    math_cfg = template["mathematics"]
    cv2.rectangle(img, (80, 1270), (w - 80, 2260), (0, 0, 0), 2)
    cv2.putText(img, "JAWABAN SOAL MATEMATIKA (1 - 100)", (w // 2 - 250, 1315), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 0), 2)

    for col_cfg in math_cfg["columns"]:
        s_q = col_cfg["start_q"]
        e_q = col_cfg["end_q"]
        sx = col_cfg["start_x"]
        sy = col_cfg["start_y"]
        q_spy = col_cfg["question_spacing_y"]
        c_spx = col_cfg["choice_spacing_x"]

        for q in range(s_q, e_q + 1):
            row = q - s_q
            qy = sy + row * q_spy
            cv2.putText(img, f"{q:02d}", (sx - 40, qy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
            for c_idx, choice in enumerate(math_cfg["choices"]):
                cx = sx + c_idx * c_spx
                cv2.circle(img, (cx, qy), math_cfg["bubble_radius"], (120, 120, 120), 1)
                cv2.putText(img, choice, (cx - 3, qy + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.28, (120, 120, 120), 1)

    return img


def fill_bubble(
    img: np.ndarray,
    cx: int,
    cy: int,
    radius: int = 10,
    darkness: int = 20,
    fill_percent: float = 0.95
) -> None:
    """Simulate filling a bubble with solid pencil mark."""
    r = int(radius * fill_percent)
    cv2.circle(img, (cx, cy), r, (darkness, darkness, darkness), -1)


def generate_student_ljk(
    template: Dict[str, Any],
    name: str = "WAHYU SURYANINGRAT",
    npm: str = "1301234567",
    faculty_index: int = 0,
    survey_answers: Optional[List[str]] = None,
    math_answers: Optional[List[str]] = None,
    warp_skew: bool = False,
    add_blur: bool = False
) -> np.ndarray:
    """
    Generate a realistic filled student LJK image with exact answers.
    """
    img = create_blank_canonical_ljk(template)
    scoring_cfg = template.get("scoring", {})
    b_rad = scoring_cfg.get("bubble_radius", 10)

    # 1. Fill Name
    name_cfg = template["identity"]["name"]
    alphabet = name_cfg["alphabet"]
    clean_name = name.upper()[:name_cfg["positions"]]
    for col, ch in enumerate(clean_name):
        if ch in alphabet:
            row = alphabet.index(ch)
            cx = name_cfg["start_x"] + col * name_cfg["spacing_x"]
            cy = name_cfg["start_y"] + row * name_cfg["spacing_y"]
            fill_bubble(img, cx, cy, b_rad)

    # 2. Fill NPM
    npm_cfg = template["identity"]["npm"]
    clean_npm = (npm + "0000000000")[:npm_cfg["digits"]]
    for col, ch in enumerate(clean_npm):
        if ch.isdigit():
            d = int(ch)
            cx = npm_cfg["start_x"] + col * npm_cfg["spacing_x"]
            cy = npm_cfg["start_y"] + d * npm_cfg["spacing_y"]
            fill_bubble(img, cx, cy, b_rad)

    # 3. Fill Faculty
    fac_cfg = template["identity"]["faculty"]
    if 0 <= faculty_index < fac_cfg["options"]:
        cx = fac_cfg["start_x"]
        cy = fac_cfg["start_y"] + faculty_index * fac_cfg["spacing_y"]
        fill_bubble(img, cx, cy, b_rad)

    # 4. Fill Survey
    surv_cfg = template["survey"]
    if survey_answers is None:
        survey_answers = ["A", "B", "C", "D", "A", "B", "C", "D", "A", "B"]

    choices = surv_cfg["choices"]
    for col_cfg in surv_cfg["columns"]:
        s_q = col_cfg["start_q"]
        e_q = col_cfg["end_q"]
        for q in range(s_q, e_q + 1):
            if q - 1 < len(survey_answers):
                ans = survey_answers[q - 1]
                if ans in choices:
                    c_idx = choices.index(ans)
                    cx = col_cfg["start_x"] + c_idx * col_cfg["choice_spacing_x"]
                    cy = col_cfg["start_y"] + (q - s_q) * col_cfg["question_spacing_y"]
                    fill_bubble(img, cx, cy, b_rad)

    # 5. Fill Math Answers
    math_cfg = template["mathematics"]
    if math_answers is None:
        math_answers = [["A", "B", "C", "D"][i % 4] for i in range(100)]

    for col_cfg in math_cfg["columns"]:
        s_q = col_cfg["start_q"]
        e_q = col_cfg["end_q"]
        for q in range(s_q, e_q + 1):
            if q - 1 < len(math_answers):
                ans = math_answers[q - 1]
                if ans in choices:
                    c_idx = choices.index(ans)
                    cx = col_cfg["start_x"] + c_idx * col_cfg["choice_spacing_x"]
                    cy = col_cfg["start_y"] + (q - s_q) * col_cfg["question_spacing_y"]
                    fill_bubble(img, cx, cy, b_rad)

    # Apply warp/skew simulation if requested
    if warp_skew:
        h, w = img.shape[:2]
        pad = 120
        padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(200, 200, 200))
        ph, pw = padded.shape[:2]

        pts1 = np.float32([[pad, pad], [w + pad, pad], [w + pad, h + pad], [pad, h + pad]])
        pts2 = np.float32([
            [pad + 40, pad + 30],
            [w + pad - 30, pad + 50],
            [w + pad - 20, h + pad - 40],
            [pad + 50, h + pad - 25]
        ])
        matrix = cv2.getPerspectiveTransform(pts1, pts2)
        img = cv2.warpPerspective(padded, matrix, (pw, ph), borderValue=(190, 190, 190))

    if add_blur:
        img = cv2.GaussianBlur(img, (3, 3), 0.8)

    return img
