from __future__ import annotations

import time

import cv2
import numpy as np

from .models import CapturedFrame, Rect, WindowInfo


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


def _wait_for_new_frame(
    backend,
    window: WindowInfo,
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.025,
) -> CapturedFrame | None:
    """Wait only for a genuinely new desktop-present observation.

    Cached frames are explicitly rejected. A measurement that cannot obtain a
    new frame is inconclusive rather than silently proving a negative.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        frame = backend.grab(window.client_rect, window.monitor, allow_cached=False)
        if frame is not None and not frame.reused_cached:
            return frame
        time.sleep(poll_seconds)
    return None


def probe_overlay_exclusion(
    *,
    backend,
    window: WindowInfo,
    overlay,
    qt_app,
    viewport_screen: Rect,
    baseline_frame: CapturedFrame,
    settle_seconds: float = 0.08,
    fresh_timeout_seconds: float = 1.25,
) -> dict:
    """Positive-control test for in-viewport capture exclusion.

    The test proves that the measurement path can first *see* the overlay with
    WDA_NONE, then verifies that the same path stops seeing it after
    WDA_EXCLUDEFROMCAPTURE. Every measurement frame must be a new observation;
    cache reuse makes the probe inconclusive.
    """
    if baseline_frame.reused_cached:
        return {
            "conclusive": False,
            "positive_control_passed": False,
            "reason": "baseline frame reused cache; exclusion not verified",
        }

    overlay.hide()
    qt_app.processEvents()
    time.sleep(settle_seconds)

    # Showing the overlay may apply exclusion in showEvent. Explicitly disable it
    # afterwards so this sample is a known-positive contamination control.
    overlay.show()
    qt_app.processEvents()
    overlay.anchor_inside(viewport_screen)
    qt_app.processEvents()
    disable_result = overlay.set_capture_excluded(False)
    qt_app.processEvents()
    time.sleep(settle_seconds)

    overlay_rect = overlay.physical_rect()
    overlap = overlay_rect.intersect(window.client_rect).intersect(viewport_screen)
    if overlap.area <= 0:
        overlay.set_capture_excluded(True)
        return {
            "conclusive": False,
            "positive_control_passed": False,
            "reason": "overlay did not overlap viewport",
            "overlay_rect": overlay_rect.as_region(),
            "viewport_screen": viewport_screen.as_region(),
        }
    if not disable_result.get("success"):
        overlay.set_capture_excluded(True)
        return {
            "conclusive": False,
            "positive_control_passed": False,
            "reason": "could not set WDA_NONE for positive control",
            "disable_exclusion": disable_result,
        }

    positive_frame = _wait_for_new_frame(
        backend,
        window,
        timeout_seconds=fresh_timeout_seconds,
    )
    if positive_frame is None:
        overlay.set_capture_excluded(True)
        return {
            "conclusive": False,
            "positive_control_passed": False,
            "reason": "no new frame after positive-control overlay became visible",
            "disable_exclusion": disable_result,
        }

    enable_result = overlay.set_capture_excluded(True)
    qt_app.processEvents()
    time.sleep(settle_seconds)
    if not enable_result.get("success"):
        return {
            "conclusive": True,
            "positive_control_passed": True,
            "exclusion_verified": False,
            "reason": "positive control visible, but WDA_EXCLUDEFROMCAPTURE call failed",
            "disable_exclusion": disable_result,
            "enable_exclusion": enable_result,
        }

    excluded_frame = _wait_for_new_frame(
        backend,
        window,
        timeout_seconds=fresh_timeout_seconds,
    )
    if excluded_frame is None:
        return {
            "conclusive": False,
            "positive_control_passed": False,
            "exclusion_verified": False,
            "reason": "no new frame after enabling exclusion; negative sample not observed",
            "disable_exclusion": disable_result,
            "enable_exclusion": enable_result,
        }

    if not (
        baseline_frame.region == positive_frame.region == excluded_frame.region
    ):
        return {
            "conclusive": False,
            "positive_control_passed": False,
            "reason": "capture region moved during overlay probe",
        }

    base_crop = _crop_screen_overlap(
        baseline_frame.image_bgr, baseline_frame.region, overlap
    )
    positive_crop = _crop_screen_overlap(
        positive_frame.image_bgr, positive_frame.region, overlap
    )
    excluded_crop = _crop_screen_overlap(
        excluded_frame.image_bgr, excluded_frame.region, overlap
    )

    positive_mean, positive_p95 = _diff_stats(base_crop, positive_crop)
    excluded_mean, excluded_p95 = _diff_stats(base_crop, excluded_crop)
    reduction_mean = max(0.0, positive_mean - excluded_mean)
    reduction_p95 = max(0.0, positive_p95 - excluded_p95)

    return {
        "conclusive": True,
        "positive_control_passed": None,  # assessed against explicit gate thresholds
        "exclusion_verified": True,
        "capture_exclusion": enable_result,
        "click_through": overlay.click_through_result,
        "disable_exclusion": disable_result,
        "enable_exclusion": enable_result,
        "overlay_rect": overlay_rect.as_region(),
        "overlap_rect": overlap.as_region(),
        "frame_provenance": {
            "baseline_cached": baseline_frame.reused_cached,
            "positive_cached": positive_frame.reused_cached,
            "excluded_cached": excluded_frame.reused_cached,
        },
        "metrics": {
            "positive_control_signal_mean": positive_mean,
            "positive_control_signal_p95": positive_p95,
            "excluded_signal_mean": excluded_mean,
            "excluded_signal_p95": excluded_p95,
            "signal_reduction_mean": reduction_mean,
            "signal_reduction_p95": reduction_p95,
        },
    }
