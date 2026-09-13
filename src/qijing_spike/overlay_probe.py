from __future__ import annotations

import time

import cv2
import numpy as np

from .models import CapturedFrame, Rect, WindowInfo
from .overlay import (
    PROBE_MARKER_BORDER_RGB,
    PROBE_MARKER_CROSS_HALF,
    PROBE_MARKER_CROSS_RGB,
    PROBE_MARKER_INSET,
    PROBE_MARKER_LINE_WIDTH,
)


MARKER_COLOR_TOLERANCE = 55


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
    """Return same-size nearby blocks for motion diagnostics only."""
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
    """Legacy/general motion evidence retained for diagnostics, not S1 verdicts."""
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

    if control_stats:
        control_mean = float(np.median([item["mean"] for item in control_stats]))
        control_p95 = float(np.median([item["p95"] for item in control_stats]))
        normalized_mean = max(0.0, target_mean - control_mean)
        normalized_p95 = max(0.0, target_p95 - control_p95)
    else:
        control_mean = None
        control_p95 = None
        normalized_mean = None
        normalized_p95 = None

    return {
        "target_mean": target_mean,
        "target_p95": target_p95,
        "control_mean": control_mean,
        "control_p95": control_p95,
        "normalized_mean": normalized_mean,
        "normalized_p95": normalized_p95,
        "controls": control_stats,
    }


def _expected_marker_masks(height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    """Build the expected border/cross geometry for any probe crop size.

    Real probes are 72x72. Scaling the geometry keeps unit-test stubs small
    without weakening the production signature definition.
    """
    if height <= 0 or width <= 0:
        return np.zeros((0, 0), dtype=np.uint8), np.zeros((0, 0), dtype=np.uint8)

    scale = min(height, width) / 72.0
    inset = max(1, int(round(PROBE_MARKER_INSET * scale)))
    line_width = max(1, int(round(PROBE_MARKER_LINE_WIDTH * scale)))
    cross_half = max(2, int(round(PROBE_MARKER_CROSS_HALF * scale)))

    border = np.zeros((height, width), dtype=np.uint8)
    cross = np.zeros((height, width), dtype=np.uint8)

    x0 = min(max(inset, 0), width - 1)
    y0 = min(max(inset, 0), height - 1)
    x1 = max(x0, width - inset - 1)
    y1 = max(y0, height - inset - 1)
    cv2.rectangle(border, (x0, y0), (x1, y1), 255, thickness=line_width)

    cx = width // 2
    cy = height // 2
    cv2.line(
        cross,
        (max(0, cx - cross_half), cy),
        (min(width - 1, cx + cross_half), cy),
        255,
        thickness=line_width,
    )
    cv2.line(
        cross,
        (cx, max(0, cy - cross_half)),
        (cx, min(height - 1, cy + cross_half)),
        255,
        thickness=line_width,
    )
    return border, cross


def _color_mask_bgr(image_bgr: np.ndarray, rgb: tuple[int, int, int]) -> np.ndarray:
    target = np.array([rgb[2], rgb[1], rgb[0]], dtype=np.int16)
    pixels = image_bgr.astype(np.int16)
    return np.all(np.abs(pixels - target) <= MARKER_COLOR_TOLERANCE, axis=2)


def _coverage(color_mask: np.ndarray, expected_mask: np.ndarray) -> tuple[int, int, float]:
    expected = expected_mask > 0
    expected_pixels = int(expected.sum())
    if expected_pixels <= 0:
        return 0, 0, 0.0
    matched = int(np.logical_and(color_mask, expected).sum())
    return matched, expected_pixels, matched / expected_pixels


def _marker_signature_stats(
    frame: CapturedFrame,
    marker_screen: Rect,
) -> dict:
    crop = _crop_screen_overlap(frame.image_bgr, frame.region, marker_screen)
    if crop.size == 0:
        return {
            "score": 0.0,
            "border_coverage": 0.0,
            "cross_coverage": 0.0,
            "border_matched_pixels": 0,
            "cross_matched_pixels": 0,
            "border_expected_pixels": 0,
            "cross_expected_pixels": 0,
        }

    border_expected, cross_expected = _expected_marker_masks(crop.shape[0], crop.shape[1])
    border_color = _color_mask_bgr(crop, PROBE_MARKER_BORDER_RGB)
    cross_color = _color_mask_bgr(crop, PROBE_MARKER_CROSS_RGB)

    border_matched, border_total, border_coverage = _coverage(border_color, border_expected)
    cross_matched, cross_total, cross_coverage = _coverage(cross_color, cross_expected)
    score = min(border_coverage, cross_coverage)
    return {
        "score": float(score),
        "border_coverage": float(border_coverage),
        "cross_coverage": float(cross_coverage),
        "border_matched_pixels": border_matched,
        "cross_matched_pixels": cross_matched,
        "border_expected_pixels": border_total,
        "cross_expected_pixels": cross_total,
        "color_tolerance": MARKER_COLOR_TOLERANCE,
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
    """Falsifiable S1 test using a known marker signature.

    The positive sample uses WDA_NONE and must contain the deliberately rendered
    magenta-border/cyan-cross marker. The excluded sample is classified by marker
    presence after WDA_EXCLUDEFROMCAPTURE. Generic image differences are retained
    only as diagnostics and cannot certify S1 either way.
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
        if overlap != overlay_rect:
            return {
                "conclusive": False,
                "reason": "probe marker was clipped; signature geometry would be incomplete",
                "overlay_rect": overlay_rect.as_region(),
                "overlap_rect": overlap.as_region(),
            }

        controls = _same_size_control_rects(viewport_screen, overlap)

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

        positive_signature = _marker_signature_stats(positive_frame, overlap)
        positive_motion = _normalized_motion(baseline_frame, positive_frame, overlap, controls)

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
                    "positive_marker_score": positive_signature["score"],
                    "positive_marker_border_coverage": positive_signature["border_coverage"],
                    "positive_marker_cross_coverage": positive_signature["cross_coverage"],
                    "positive_marker_signature": positive_signature,
                    "positive_control_signal_mean": positive_motion["normalized_mean"],
                    "positive_control_signal_p95": positive_motion["normalized_p95"],
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

        excluded_signature = _marker_signature_stats(excluded_frame, overlap)
        excluded_motion = _normalized_motion(baseline_frame, excluded_frame, overlap, controls)
        positive_score = positive_signature["score"]
        excluded_score = excluded_signature["score"]
        retained_fraction = excluded_score / positive_score if positive_score > 1e-9 else None

        return {
            "conclusive": True,
            "capture_exclusion": enable_result,
            "click_through": overlay.click_through_result,
            "disable_exclusion": disable_result,
            "enable_exclusion": enable_result,
            "probe_geometry": "SIGNED_MAGENTA_BORDER_CYAN_CROSS",
            "overlay_rect": overlay_rect.as_region(),
            "overlap_rect": overlap.as_region(),
            "control_rects": [rect.as_region() for rect in controls],
            "frame_provenance": {
                "baseline_cached": baseline_frame.reused_cached,
                "positive_cached": positive_frame.reused_cached,
                "excluded_cached": excluded_frame.reused_cached,
            },
            "metrics": {
                # Verdict metrics: known marker presence, independent of game motion.
                "positive_marker_score": positive_score,
                "excluded_marker_score": excluded_score,
                "marker_retained_fraction": retained_fraction,
                "positive_marker_border_coverage": positive_signature["border_coverage"],
                "positive_marker_cross_coverage": positive_signature["cross_coverage"],
                "excluded_marker_border_coverage": excluded_signature["border_coverage"],
                "excluded_marker_cross_coverage": excluded_signature["cross_coverage"],
                "positive_marker_signature": positive_signature,
                "excluded_marker_signature": excluded_signature,
                # Generic motion metrics are diagnostic only.
                "positive_control_signal_mean": positive_motion["normalized_mean"],
                "positive_control_signal_p95": positive_motion["normalized_p95"],
                "excluded_signal_mean": excluded_motion["normalized_mean"],
                "excluded_signal_p95": excluded_motion["normalized_p95"],
                "signal_reduction_mean": (
                    max(0.0, positive_motion["normalized_mean"] - excluded_motion["normalized_mean"])
                    if positive_motion["normalized_mean"] is not None
                    and excluded_motion["normalized_mean"] is not None
                    else None
                ),
                "positive_target_mean": positive_motion["target_mean"],
                "positive_control_motion_mean": positive_motion["control_mean"],
                "excluded_target_mean": excluded_motion["target_mean"],
                "excluded_control_motion_mean": excluded_motion["control_mean"],
            },
        }
    finally:
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
