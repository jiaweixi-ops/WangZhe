from __future__ import annotations

import cv2
import numpy as np

from .models import Rect, ViewportResult


def _fit_centered_aspect(rect: Rect, aspect: float) -> Rect:
    if rect.width <= 0 or rect.height <= 0 or aspect <= 0:
        return rect
    current = rect.width / rect.height
    if current > aspect:
        width = int(round(rect.height * aspect))
        left = rect.left + (rect.width - width) // 2
        return Rect(left, rect.top, left + width, rect.bottom)
    height = int(round(rect.width / aspect))
    top = rect.top + (rect.height - height) // 2
    return Rect(rect.left, top, rect.right, top + height)


def detect_content_viewport(
    image_bgr: np.ndarray,
    *,
    black_mean_threshold: float = 10.0,
    black_std_threshold: float = 10.0,
    min_content_fraction: float = 0.45,
    expected_aspect_ratio: float | None = None,
) -> ViewportResult:
    """Locate the likely game viewport and expose uncertainty explicitly.

    Black letter/pillar boxes are handled directly. If an expected game aspect
    is supplied, a centered aspect prior can be used as a degraded fallback for
    emulator/stream chrome; this is deliberately reported as lower confidence.
    """
    if image_bgr.ndim != 3:
        raise ValueError("Expected BGR image")
    h, w = image_bgr.shape[:2]
    full = Rect(0, 0, w, h)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    row_mean = gray.mean(axis=1)
    row_std = gray.std(axis=1)
    col_mean = gray.mean(axis=0)
    col_std = gray.std(axis=0)

    row_content = (row_mean > black_mean_threshold) | (row_std > black_std_threshold)
    col_content = (col_mean > black_mean_threshold) | (col_std > black_std_threshold)

    if not row_content.any() or not col_content.any():
        return ViewportResult(
            rect=full,
            mode="FAILED_ALL_BLACK",
            confidence=0.0,
            reason="no content rows/columns detected",
        )

    ys = np.flatnonzero(row_content)
    xs = np.flatnonzero(col_content)
    candidate = Rect(int(xs[0]), int(ys[0]), int(xs[-1] + 1), int(ys[-1] + 1))
    fraction = candidate.area / max(full.area, 1)

    trimmed = candidate != full
    if fraction >= min_content_fraction and trimmed:
        return ViewportResult(
            rect=candidate,
            mode="BLACK_BORDER",
            confidence=0.90,
            reason=f"trimmed near-black border; content_fraction={fraction:.3f}",
        )

    if expected_aspect_ratio:
        prior = _fit_centered_aspect(full, expected_aspect_ratio)
        prior_fraction = prior.area / max(full.area, 1)
        if prior != full:
            return ViewportResult(
                rect=prior,
                mode="ASPECT_PRIOR",
                confidence=0.40,
                reason=(
                    "no reliable black border; centered expected-aspect fallback; "
                    f"viewport_fraction={prior_fraction:.3f}"
                ),
            )

    if fraction < min_content_fraction:
        return ViewportResult(
            rect=full,
            mode="UNCERTAIN_LOW_FRACTION",
            confidence=0.10,
            reason=(
                f"detected content_fraction={fraction:.3f} below minimum; "
                "not silently cropping"
            ),
        )

    return ViewportResult(
        rect=full,
        mode="FULL_FRAME",
        confidence=0.60,
        reason="no removable black border detected",
    )


def crop_rect(image_bgr: np.ndarray, rect: Rect) -> np.ndarray:
    return image_bgr[rect.top : rect.bottom, rect.left : rect.right]
