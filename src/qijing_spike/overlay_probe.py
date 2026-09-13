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


def _same_size_control_rects(viewport: Rect, target: Rect, gap: int = 12) -> list[Rect]:
    """Return same-size nearby blocks used to estimate ordinary game motion."""
    w, h = target.width, target.height
    candidates = [
        Rect(target.right + gap, target.top, target.right + gap + w, target.bottom),
        Rect(target.left - gap - w, target.top, target.left - gap, target.bottom),
        Rect(target.left, target.bottom + gap, target.right, target.bottom + gap + h),
        Rect(target.left, target.top - gap - h, target.right, target.top - gap),
    ]
    controls: list[Rect] = []
    for candidate in candidates:
        if candidate.area <= 0:
            continue
        if candidate.intersect(viewport) != candidate:
            continue
        if candidate.intersect(target).area > 0:
            continue
        controls.append(candidate)
    return controls


def _normalized_motion(
    baseline: CapturedFrame,
    sample: CapturedFrame,
    target: Rect,
    controls: list[Rect],
) -> dict:
    target_base = _crop_screen_overlap(baseline.image_bgr, baseline.region, target)
    target_sample = _crop_screen_overlap(sample.image_bgr, sample.region, target)
    target_mean, target_p95 = _diff_stats(target_base, target_sample)

    control_stats = []
    for rect in controls:
        control_base = _crop_screen_overlap(baseline.image_bgr, baseline.region, rect)
        control_sample = _crop_screen_overlap(sample.image_bgr, sample.region, rect)
        mean_delta, p95_delta = _diff_stats(control_base, control_sample)
        control_stats.append(
            {
                "rect": rect.as_region(),
                "mean": mean_delta,
                "p95": p95_delta,
            }
        )

    control_mean = float(np.median([item["mean"] for item in control_stats]))
    control_p95 = float(np.median([item["p95"] for item in control_stats]))
    return {
        "target_mean": target_mean,
        "target_p95": target_p95,
        "control_mean": control_mean,
        "control_p95": control_p95,
        "normalized_mean": max(0.0, target_mean - control_mean),
        "normalized_p95": max(0.0, target_p95 - control_p95),
        "controls": control_stats,
    }


def _wait_for_new_frame(
    backend,
    window: WindowInfo,
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.025,
) -> CapturedFrame | None:
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
    """Falsifiable S1 test using a product-like marker and spatial controls.

    The positive sample uses WDA_NONE and must produce marker-specific signal
    beyond ordinary motion measured in same-size nearby control blocks. The
    excluded sample repeats the same measurement after WDA_EXCLUDEFROMCAPTURE.
    Cached frames are never accepted as measurements.
    """
    if baseline_frame.reused_cached:
        return {
            "conclusive": False,
            "reason": "baseline frame reused cache; exclusion not verified",
        }

    marker_mode_available = hasattr(overlay, "set_probe_marker_mode")
    enable_result = None
    disable_result = None
    try:
        overlay.hide()
        if marker_mode_available:
            overlay.set_probe_marker_mode(True)
        qt_app.processEvents()
        time.sleep(settle_seconds)

        overlay.show()
        qt_app.processEvents()
        overlay.anchor_inside(viewport_screen)
        qt_app.processEvents()
        time.sleep(settle_seconds)

        overlay_rect = overlay.physical_rect()
        overlap = overlay_rect.intersect(window.client_rect).intersect(viewport_screen)
        if overlap.area <= 0:
            return {
                "conclusive": False,
                "reason": "probe marker did not overlap viewport",
                "overlay_rect": overlay_rect.as_region(),
                "viewport_screen": viewport_screen.as_region(),
            }

        controls = _same_size_control_rects(viewport_screen, overlap)
        if not controls:
            return {
                "conclusive": False,
                "reason": "no same-size control block fits inside viewport",
                "overlay_rect": overlay_rect.as_region(),
                "overlap_rect": overlap.as_region(),
            }

        disable_result = overlay.set_capture_excluded(False)
        qt_app.processEvents()
        time.sleep(settle_seconds)
        if not disable_result.get("success"):
            return {
                "conclusive": False,
                "reason": "could not set WDA_NONE for positive control",
                "disable_exclusion": disable_result,
            }

        positive_frame = _wait_for_new_frame(
            backend,
            window,
            timeout_seconds=fresh_timeout_seconds,
        )
        if positive_frame is None:
            return {
                "conclusive": False,
                "reason": "no new frame after positive-control marker became visible",
                "disable_exclusion": disable_result,
            }
        if positive_frame.region != baseline_frame.region:
            return {
                "conclusive": False,
                "reason": "capture region moved before positive-control sample",
            }

        positive = _normalized_motion(baseline_frame, positive_frame, overlap, controls)

        enable_result = overlay.set_capture_excluded(True)
        qt_app.processEvents()
        time.sleep(settle_seconds)
        if not enable_result.get("success"):
            return {
                "conclusive": True,
                "reason": "positive sample observed, but WDA_EXCLUDEFROMCAPTURE call failed",
                "capture_exclusion": enable_result,
                "disable_exclusion": disable_result,
                "enable_exclusion": enable_result,
                "overlay_rect": overlay_rect.as_region(),
                "overlap_rect": overlap.as_region(),
                "control_rects": [rect.as_region() for rect in controls],
                "metrics": {
                    "positive_control_signal_mean": positive["normalized_mean"],
                    "positive_control_signal_p95": positive["normalized_p95"],
                    "positive_target_mean": positive["target_mean"],
                    "positive_control_motion_mean": positive["control_mean"],
                },
            }

        excluded_frame = _wait_for_new_frame(
            backend,
            window,
            timeout_seconds=fresh_timeout_seconds,
        )
        if excluded_frame is None:
            return {
                "conclusive": False,
                "reason": "no new frame after enabling exclusion; negative sample not observed",
                "disable_exclusion": disable_result,
                "enable_exclusion": enable_result,
            }
        if excluded_frame.region != baseline_frame.region:
            return {
                "conclusive": False,
                "reason": "capture region moved before excluded sample",
            }

        excluded = _normalized_motion(baseline_frame, excluded_frame, overlap, controls)
        positive_mean = positive["normalized_mean"]
        positive_p95 = positive["normalized_p95"]
        excluded_mean = excluded["normalized_mean"]
        excluded_p95 = excluded["normalized_p95"]

        return {
            "conclusive": True,
            "capture_exclusion": enable_result,
            "click_through": overlay.click_through_result,
            "disable_exclusion": disable_result,
            "enable_exclusion": enable_result,
            "probe_geometry": "SMALL_MARKER",
            "overlay_rect": overlay_rect.as_region(),
            "overlap_rect": overlap.as_region(),
            "control_rects": [rect.as_region() for rect in controls],
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
                "signal_reduction_mean": max(0.0, positive_mean - excluded_mean),
                "signal_reduction_p95": max(0.0, positive_p95 - excluded_p95),
                "positive_target_mean": positive["target_mean"],
                "positive_control_motion_mean": positive["control_mean"],
                "excluded_target_mean": excluded["target_mean"],
                "excluded_control_motion_mean": excluded["control_mean"],
            },
        }
    finally:
        # Leave the product in its normal safe state even when the measurement
        # exits early. The returned evidence preserves the actual tested calls.
        try:
            overlay.set_capture_excluded(True)
        except Exception:
            pass
        if marker_mode_available:
            try:
                overlay.set_probe_marker_mode(False)
            except Exception:
                pass
        try:
            overlay.show()
            qt_app.processEvents()
        except Exception:
            pass
