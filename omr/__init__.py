"""
OMR Engine package for Telkom University LJK processing.
100% Deterministic OpenCV implementation without OCR or AI.
"""

from .pipeline import process_ljk

__all__ = ["process_ljk"]
