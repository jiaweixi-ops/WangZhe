from __future__ import annotations

import cv2
import numpy as np

from .models import Rect


def detect_content_viewport(
    image_bgr: np.ndarray,
    *,
    black_mean_threshold: float = 10.0,
    black_std_threshold: float = 10.0,
    min_content_fraction: float = 0.45,
) -> Rect:
    """Trim nearly-black, low-variance letterbox/pillarbox borders."""
    if image_bgr.ndim != 3:
        raise ValueError("Expected BGR image")
    h, w = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    row_mean = gray.mean(axis=1)
    row_std = gray.std(axis=1)
    col_mean = gray.mean(axis=0)
    col_std = gray.std(axis=0)

    row_content = (row_mean > black_mean_threshold) | (row_std > black_std_threshold)
    col_content = (col_mean > black_mean_threshold) | (col_std > black_std_threshold)

    if not row_content.any() or not col_content.any():
        return Rect(0, 0, w, h)

    ys = np.flatnonzero(row_content)
    xs = np.flatnonzero(col_content)
    top, bottom = int(ys[0]), int(ys[-1] + 1)
    left, right = int(xs[0]), int(xs[-1] + 1)

    if (right - left) * (bottom - top) < w * h * min_content_fraction:
        return Rect(0, 0, w, h)

    return Rect(left, top, right, bottom)


def crop_rect(image_bgr: np.ndarray, rect: Rect) -> np.ndarray:
    return image_bgr[rect.top:rect.bottom, rect.left:rect.right]
