from __future__ import annotations

import time

import cv2
import numpy as np

from .models import Rect, WindowInfo


def _crop_screen_overlap(image: np.ndarray, capture_region: Rect, overlap_screen: Rect) -> np.ndarray:
    local = Rect(
        overlap_screen.left - capture_region.left,
        overlap_screen.top - capture_region.top,
        overlap_screen.right - capture_region.left,
        overlap_screen.bottom - capture_region.top,
    )
    return image[local.top : local.bottom, local.left : local.right]


def _diff_stats(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    if a.shape != b.shape or a.size == 0:
        return float("inf"), float("inf")
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(ga, gb)
    return float(diff.mean()), float(np.percentile(diff, 95))


def probe_overlay_exclusion(
    *,
    backend,
    window: WindowInfo,
    overlay,
    qt_app,
    viewport_screen: Rect,
    settle_seconds: float = 0.12,
) -> dict:
    """Hide/show/hide probe inside the game viewport.

    F0 and F2 estimate background motion. F1 is captured with the overlay visible.
    A clean exclusion should make F1 no more different than the hidden baseline.
    Cached capture is allowed here so a truly static game frame does not make an
    otherwise successful capture-exclusion test inconclusive.
    """
    overlay.hide()
    qt_app.processEvents()
    time.sleep(settle_seconds)
    f0 = backend.grab(window.client_rect, window.monitor, allow_cached=True)
    if f0 is None:
        return {"conclusive": False, "reason": "no hidden baseline frame F0"}

    overlay.show()
    qt_app.processEvents()
    overlay.anchor_inside(viewport_screen)
    qt_app.processEvents()
    time.sleep(settle_seconds)
    overlay_rect = overlay.physical_rect()
    overlap = overlay_rect.intersect(window.client_rect).intersect(viewport_screen)
    f1 = backend.grab(window.client_rect, window.monitor, allow_cached=True)
    if f1 is None or overlap.area <= 0:
        return {
            "conclusive": False,
            "reason": "no shown frame F1 or overlay did not overlap viewport",
            "overlay_rect": overlay_rect.as_region(),
            "viewport_screen": viewport_screen.as_region(),
        }

    overlay.hide()
    qt_app.processEvents()
    time.sleep(settle_seconds)
    f2 = backend.grab(window.client_rect, window.monitor, allow_cached=True)
    if f2 is None:
        return {"conclusive": False, "reason": "no hidden baseline frame F2"}

    if not (f0.region == f1.region == f2.region):
        return {
            "conclusive": False,
            "reason": "capture region moved during overlay probe",
        }

    c0 = _crop_screen_overlap(f0.image_bgr, f0.region, overlap)
    c1 = _crop_screen_overlap(f1.image_bgr, f1.region, overlap)
    c2 = _crop_screen_overlap(f2.image_bgr, f2.region, overlap)

    hidden_mean, hidden_p95 = _diff_stats(c0, c2)
    shown0_mean, shown0_p95 = _diff_stats(c0, c1)
    shown2_mean, shown2_p95 = _diff_stats(c2, c1)
    shown_mean = min(shown0_mean, shown2_mean)
    shown_p95 = min(shown0_p95, shown2_p95)
    signal_mean = max(0.0, shown_mean - hidden_mean)
    signal_p95 = max(0.0, shown_p95 - hidden_p95)

    exclusion = overlay.capture_exclusion_result
    click_through = overlay.click_through_result
    overlay.show()
    overlay.anchor_inside(viewport_screen)
    qt_app.processEvents()

    return {
        "conclusive": hidden_mean <= 12.0,
        "capture_exclusion": exclusion,
        "click_through": click_through,
        "overlay_rect": overlay_rect.as_region(),
        "overlap_rect": overlap.as_region(),
        "metrics": {
            "hidden_baseline_mean": hidden_mean,
            "hidden_baseline_p95": hidden_p95,
            "shown_vs_hidden_mean": shown_mean,
            "shown_vs_hidden_p95": shown_p95,
            "overlay_signal_mean": signal_mean,
            "overlay_signal_p95": signal_p95,
        },
    }
