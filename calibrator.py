import streamlit as st
import cv2
import numpy as np
import pandas as pd
import json
from PIL import Image
from io import BytesIO


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="OMR Template Calibrator",
    page_icon="📝",
    layout="wide"
)

st.title("📝 OMR Template Calibrator")
st.caption(
    "Kalibrasi template LJK menggunakan lembar LJK yang sudah diarsir."
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def pil_to_cv(image):
    """PIL RGB -> OpenCV BGR"""
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def cv_to_pil(image):
    """OpenCV BGR/GRAY -> PIL"""
    if len(image.shape) == 2:
        return Image.fromarray(image)
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def order_points(pts):
    """
    Order points:
    top-left, top-right, bottom-right, bottom-left
    """
    rect = np.zeros((4, 2), dtype=np.float32)

    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]

    return rect


def find_page_contour(image):
    """
    Detect largest quadrilateral contour as paper.
    """

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Slight blur to suppress noise
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    edges = cv2.Canny(blur, 50, 150)

    kernel = np.ones((5, 5), np.uint8)
    edges = cv2.dilate(edges, kernel, iterations=1)
    edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=2
    )

    contours, _ = cv2.findContours(
        edges,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    if not contours:
        return None

    h, w = gray.shape
    image_area = h * w

    candidates = []

    for contour in contours:
        area = cv2.contourArea(contour)

        if area < image_area * 0.15:
            continue

        perimeter = cv2.arcLength(contour, True)

        if perimeter == 0:
            continue

        approx = cv2.approxPolyDP(
            contour,
            0.02 * perimeter,
            True
        )

        if len(approx) == 4:
            candidates.append((area, approx))

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return candidates[0][1].reshape(4, 2).astype(np.float32)


def perspective_transform(image, corners, width=1700, height=2400):
    """
    Warp paper to standardized coordinate system.
    """

    rect = order_points(corners)

    dst = np.array([
        [0, 0],
        [width - 1, 0],
        [width - 1, height - 1],
        [0, height - 1]
    ], dtype=np.float32)

    matrix = cv2.getPerspectiveTransform(
        rect,
        dst
    )

    warped = cv2.warpPerspective(
        image,
        matrix,
        (width, height)
    )

    return warped, matrix


def detect_bubbles(
    image,
    min_area=100,
    max_area=5000,
    min_circularity=0.35,
    min_aspect=0.5,
    max_aspect=2.0
):
    """
    Detect bubble/marked regions.

    The detector intentionally combines contour geometry
    with morphology so that it can work with printed LJKs.
    """

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Adaptive threshold
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        10
    )

    # Remove tiny noise
    kernel = np.ones((3, 3), np.uint8)

    binary = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        kernel,
        iterations=1
    )

    binary = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1
    )

    contours, _ = cv2.findContours(
        binary,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    detections = []

    for contour in contours:

        area = cv2.contourArea(contour)

        if area < min_area or area > max_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)

        if h == 0:
            continue

        aspect = w / float(h)

        if aspect < min_aspect or aspect > max_aspect:
            continue

        perimeter = cv2.arcLength(
            contour,
            True
        )

        if perimeter == 0:
            continue

        circularity = (
            4 * np.pi * area /
            (perimeter * perimeter)
        )

        if circularity < min_circularity:
            continue

        M = cv2.moments(contour)

        if M["m00"] == 0:
            continue

        cx = M["m10"] / M["m00"]
        cy = M["m01"] / M["m00"]

        detections.append({
            "x": int(x),
            "y": int(y),
            "w": int(w),
            "h": int(h),
            "cx": float(cx),
            "cy": float(cy),
            "area": float(area),
            "circularity": float(circularity)
        })

    return detections, binary


def remove_duplicate_detections(
    detections,
    distance_threshold=20
):
    """
    Merge detections that are very close.
    """

    if not detections:
        return []

    detections = sorted(
        detections,
        key=lambda x: x["area"],
        reverse=True
    )

    selected = []

    for det in detections:

        duplicate = False

        for existing in selected:

            dx = det["cx"] - existing["cx"]
            dy = det["cy"] - existing["cy"]

            distance = np.sqrt(
                dx * dx + dy * dy
            )

            if distance < distance_threshold:
                duplicate = True
                break

        if not duplicate:
            selected.append(det)

    return selected


def group_into_rows(
    detections,
    row_tolerance=30
):
    """
    Group bubble centers into horizontal rows.
    """

    if not detections:
        return []

    detections = sorted(
        detections,
        key=lambda x: x["cy"]
    )

    rows = []

    for det in detections:

        placed = False

        for row in rows:

            mean_y = np.mean([
                x["cy"] for x in row
            ])

            if abs(det["cy"] - mean_y) <= row_tolerance:
                row.append(det)
                placed = True
                break

        if not placed:
            rows.append([det])

    # Sort each row from left to right
    for row in rows:
        row.sort(key=lambda x: x["cx"])

    # Sort rows top to bottom
    rows.sort(
        key=lambda row: np.mean(
            [x["cy"] for x in row]
        )
    )

    return rows


def calculate_fill_ratio(gray, det):
    """
    Calculate darkness/fill ratio inside detected region.
    """

    x = det["x"]
    y = det["y"]
    w = det["w"]
    h = det["h"]

    pad = max(
        1,
        int(min(w, h) * 0.15)
    )

    x1 = max(0, x + pad)
    y1 = max(0, y + pad)
    x2 = min(gray.shape[1], x + w - pad)
    y2 = min(gray.shape[0], y + h - pad)

    roi = gray[y1:y2, x1:x2]

    if roi.size == 0:
        return 0.0

    # Normalize darkness
    darkness = 255 - np.mean(roi)

    return float(darkness / 255.0)


def draw_detections(
    image,
    rows,
    show_labels=True
):
    """
    Draw calibration overlay.
    """

    output = image.copy()

    colors = [
        (0, 255, 0),
        (255, 0, 0),
        (0, 165, 255),
        (255, 0, 255),
        (255, 255, 0)
    ]

    option_names = ["A", "B", "C", "D", "E", "F", "G"]

    for q_idx, row in enumerate(rows):

        color = colors[q_idx % len(colors)]

        for opt_idx, det in enumerate(row):

            x = det["x"]
            y = det["y"]
            w = det["w"]
            h = det["h"]

            cv2.rectangle(
                output,
                (x, y),
                (x + w, y + h),
                color,
                3
            )

            if show_labels:

                label = f"Q{q_idx + 1}"

                if opt_idx < len(option_names):
                    label += f"-{option_names[opt_idx]}"

                cv2.putText(
                    output,
                    label,
                    (x, max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    color,
                    2,
                    cv2.LINE_AA
                )

    return output


def create_calibration_json(
    image,
    rows,
    image_name
):

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY
    )

    option_names = [
        "A", "B", "C", "D", "E", "F", "G"
    ]

    questions = []

    for q_idx, row in enumerate(rows):

        options = []

        for opt_idx, det in enumerate(row):

            fill_ratio = calculate_fill_ratio(
                gray,
                det
            )

            option = {
                "option": (
                    option_names[opt_idx]
                    if opt_idx < len(option_names)
                    else str(opt_idx + 1)
                ),
                "center": [
                    round(det["cx"], 2),
                    round(det["cy"], 2)
                ],
                "bbox": [
                    det["x"],
                    det["y"],
                    det["w"],
                    det["h"]
                ],
                "area": round(det["area"], 2),
                "circularity": round(
                    det["circularity"],
                    4
                ),
                "fill_ratio": round(
                    fill_ratio,
                    4
                )
            }

            options.append(option)

        questions.append({
            "question": q_idx + 1,
            "options": options
        })

    calibration = {
        "version": "1.0",
        "template_name": image_name,
        "canvas": {
            "width": int(image.shape[1]),
            "height": int(image.shape[0])
        },
        "question_count": len(questions),
        "questions": questions
    }

    return calibration


def encode_image(image):
    """
    OpenCV -> PNG bytes
    """

    success, buffer = cv2.imencode(
        ".png",
        image
    )

    if not success:
        return None

    return buffer.tobytes()


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("⚙️ Calibration Settings")

output_width = st.sidebar.number_input(
    "Output Width",
    min_value=500,
    max_value=4000,
    value=1700,
    step=100
)

output_height = st.sidebar.number_input(
    "Output Height",
    min_value=500,
    max_value=5000,
    value=2400,
    step=100
)

st.sidebar.divider()

st.sidebar.subheader("Bubble Detection")

min_area = st.sidebar.slider(
    "Minimum Area",
    20,
    5000,
    100,
    10
)

max_area = st.sidebar.slider(
    "Maximum Area",
    100,
    20000,
    5000,
    100
)

min_circularity = st.sidebar.slider(
    "Minimum Circularity",
    0.0,
    1.0,
    0.35,
    0.05
)

row_tolerance = st.sidebar.slider(
    "Row Tolerance",
    5,
    100,
    30,
    5
)

duplicate_distance = st.sidebar.slider(
    "Duplicate Distance",
    5,
    100,
    20,
    5
)


# ============================================================
# FILE UPLOAD
# ============================================================

uploaded_files = st.file_uploader(
    "Upload LJK calibration sheet",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True
)


if not uploaded_files:

    st.info(
        "Upload satu atau beberapa foto LJK yang sudah diarsir "
        "untuk memulai proses kalibrasi."
    )

    st.markdown(
        """
        ### Alur

        **Upload → Detect Page → Perspective Correction → 
        Detect Bubble → Group Question → Export Template**

        💡 Untuk hasil terbaik, gunakan satu lembar LJK sebagai
        **calibration sheet** dengan seluruh bubble/opsi sudah
        diarsir.
        """
    )

    st.stop()


# ============================================================
# PROCESS FILES
# ============================================================

results = []

for file in uploaded_files:

    try:

        image_pil = Image.open(file).convert("RGB")
        image = pil_to_cv(image_pil)

        with st.spinner(
            f"Processing {file.name}..."
        ):

            # ---------------------------------------------
            # PAGE DETECTION
            # ---------------------------------------------

            corners = find_page_contour(image)

            if corners is not None:

                warped, matrix = perspective_transform(
                    image,
                    corners,
                    width=int(output_width),
                    height=int(output_height)
                )

                page_detected = True

            else:

                # fallback
                warped = cv2.resize(
                    image,
                    (
                        int(output_width),
                        int(output_height)
                    )
                )

                page_detected = False

            # ---------------------------------------------
            # BUBBLE DETECTION
            # ---------------------------------------------

            detections, binary = detect_bubbles(
                warped,
                min_area=min_area,
                max_area=max_area,
                min_circularity=min_circularity
            )

            detections = remove_duplicate_detections(
                detections,
                distance_threshold=duplicate_distance
            )

            rows = group_into_rows(
                detections,
                row_tolerance=row_tolerance
            )

            overlay = draw_detections(
                warped,
                rows
            )

            calibration = create_calibration_json(
                warped,
                rows,
                file.name
            )

            results.append({
                "name": file.name,
                "original": image,
                "warped": warped,
                "binary": binary,
                "overlay": overlay,
                "detections": detections,
                "rows": rows,
                "calibration": calibration,
                "page_detected": page_detected
            })

    except Exception as e:

        st.error(
            f"Error processing {file.name}: {str(e)}"
        )


# ============================================================
# RESULTS
# ============================================================

st.divider()

st.header("📊 Calibration Results")


for result in results:

    st.subheader(
        f"📄 {result['name']}"
    )

    detections = result["detections"]
    rows = result["rows"]

    # --------------------------------------------------------
    # METRICS
    # --------------------------------------------------------

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "Page",
        "Detected" if result["page_detected"]
        else "Fallback"
    )

    col2.metric(
        "Detected Areas",
        len(detections)
    )

    col3.metric(
        "Rows / Questions",
        len(rows)
    )

    if rows:
        avg_options = np.mean([
            len(row) for row in rows
        ])
    else:
        avg_options = 0

    col4.metric(
        "Avg Options / Row",
        f"{avg_options:.1f}"
    )

    # --------------------------------------------------------
    # VISUALIZATION
    # --------------------------------------------------------

    tab1, tab2, tab3, tab4 = st.tabs([
        "🎯 Overlay",
        "📐 Warped",
        "⚫ Threshold",
        "📋 Detection Data"
    ])

    with tab1:

        st.image(
            cv_to_pil(result["overlay"]),
            use_container_width=True
        )

    with tab2:

        st.image(
            cv_to_pil(result["warped"]),
            use_container_width=True
        )

    with tab3:

        st.image(
            result["binary"],
            use_container_width=True
        )

    with tab4:

        table_rows = []

        for q_idx, row in enumerate(rows):

            for opt_idx, det in enumerate(row):

                fill_ratio = calculate_fill_ratio(
                    cv2.cvtColor(
                        result["warped"],
                        cv2.COLOR_BGR2GRAY
                    ),
                    det
                )

                table_rows.append({
                    "Question": q_idx + 1,
                    "Option": chr(
                        65 + opt_idx
                    ),
                    "X": round(
                        det["cx"],
                        2
                    ),
                    "Y": round(
                        det["cy"],
                        2
                    ),
                    "Width": det["w"],
                    "Height": det["h"],
                    "Area": round(
                        det["area"],
                        1
                    ),
                    "Circularity": round(
                        det["circularity"],
                        3
                    ),
                    "Fill Ratio": round(
                        fill_ratio,
                        3
                    )
                })

        if table_rows:

            df = pd.DataFrame(
                table_rows
            )

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True
            )

        else:

            st.warning(
                "Tidak ada area yang berhasil dideteksi."
            )

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    json_data = json.dumps(
        result["calibration"],
        indent=2
    )

    overlay_bytes = encode_image(
        result["overlay"]
    )

    col1, col2 = st.columns(2)

    with col1:

        st.download_button(
            label="⬇️ Download Calibration JSON",
            data=json_data,
            file_name=(
                result["name"]
                .rsplit(".", 1)[0]
                + "_template.json"
            ),
            mime="application/json",
            key=(
                "json_"
                + result["name"]
            )
        )

    with col2:

        st.download_button(
            label="⬇️ Download Overlay PNG",
            data=overlay_bytes,
            file_name=(
                result["name"]
                .rsplit(".", 1)[0]
                + "_overlay.png"
            ),
            mime="image/png",
            key=(
                "overlay_"
                + result["name"]
            )
        )

    st.divider()


# ============================================================
# SUMMARY
# ============================================================

st.header("📈 Summary")

summary = []

for result in results:

    rows = result["rows"]

    summary.append({
        "File": result["name"],
        "Page Detected": result["page_detected"],
        "Detected Areas": len(
            result["detections"]
        ),
        "Questions": len(rows),
        "Min Options": (
            min(
                [len(x) for x in rows]
            )
            if rows else 0
        ),
        "Max Options": (
            max(
                [len(x) for x in rows]
            )
            if rows else 0
        )
    })

summary_df = pd.DataFrame(summary)

st.dataframe(
    summary_df,
    use_container_width=True,
    hide_index=True
)
