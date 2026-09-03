"""
Preprocessing module.
Deterministic image transformations for optimal bubble extraction.
"""

from typing import Dict, Tuple
import numpy as np
import cv2


def preprocess_aligned_image(
    aligned_img: np.ndarray
) -> Dict[str, np.ndarray]:
    """
    Perform deterministic contrast normalization and binarization on aligned canonical image.
    Returns dictionary with:
    - 'gray': Grayscale normalized image
    - 'inv_gray': Inverted grayscale image (pencil marks = bright)
    - 'binary_otsu': Otsu inverted threshold
    - 'binary_adaptive': Adaptive inverted threshold
    - 'binary_combined': Robust combined binary map
    """
    if len(aligned_img.shape) == 3:
        gray = cv2.cvtColor(aligned_img, cv2.COLOR_BGR2GRAY)
    else:
        gray = aligned_img.copy()

    # Subtle Gaussian smoothing to reduce sensor noise
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)

    # Inverted grayscale (dark marks become high pixel values)
    inv_gray = cv2.bitwise_not(blurred)

    # 1. Otsu threshold (inverted: dark marks -> 255, white paper -> 0)
    _, binary_otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 2. Adaptive Gaussian threshold (inverted: helps on faint pencil marks)
    binary_adaptive = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        10
    )

    # 3. Robust Combined binary map
    binary_combined = cv2.bitwise_or(binary_otsu, binary_adaptive)

    return {
        "gray": blurred,
        "inv_gray": inv_gray,
        "binary_otsu": binary_otsu,
        "binary_adaptive": binary_adaptive,
        "binary_combined": binary_combined
    }
