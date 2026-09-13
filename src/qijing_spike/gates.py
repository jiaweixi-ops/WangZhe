from __future__ import annotations

from collections import Counter
from statistics import mean

import numpy as np

from .models import GateResult


SPIKE_THRESHOLDS = {
    "s0": {
        "pass_min_new_present_frames": 5,
        "pass_max_black_rate": 0.001,
        "pass_max_stale_rate": 0.01,
        "pass_max_capture_error_rate": 0.01,
        "degraded_max_black_rate": 0.01,
        "degraded_max_stale_rate": 0.10,
        "degraded_max_capture_error_rate": 0.05,
    },
    "s1": {
        "min_positive_control_signal_mean": 6.0,
        "min_positive_control_signal_p95": 24.0,
        "max_excluded_signal_mean": 4.0,
        "max_excluded_signal_p95": 18.0,
        "min_signal_reduction_mean": 4.0,
    },
    "s2": {
        "pass_min_accepted_rate": 0.95,
        "pass_max_failed_rate": 0.01,
        "pass_max_scale_only_rate": 0.10,
        "degraded_min_accepted_rate": 0.80,
        "degraded_max_failed_rate": 0.20,
    },
}


def _rate(n: int, d: int) -> float:
    return n / d if d else 0.0


def assess_s0(
    *,
    health_rows: list[dict],
    no_new_presents: int,
    capture_errors: int,
    gap_count: int,
) -> dict:
    """Assess S0 without treating event-driven DXGI idleness as failure.

    DXcam returns None when there is no newly presented desktop frame. That is
    recorded as liveness evidence but is not a capture error. Likewise, long
    intervals between new presents are informational until a stage-aware witness
    says the game *should* be changing.
    """
    captured = len(health_rows)
    poll_attempts = captured + no_new_presents
    total_operations = poll_attempts + capture_errors
    counts = Counter(row["freshness"] for row in health_rows)
    metrics = {
        "captured_new_present_frames": captured,
        "poll_attempts": poll_attempts,
        "no_new_present_count": no_new_presents,
        "no_new_present_rate": _rate(no_new_presents, poll_attempts),
        "capture_error_count": capture_errors,
        "capture_error_rate": _rate(capture_errors, total_operations),
        "black_rate": _rate(counts.get("BLACK", 0), captured),
        "stale_rate": _rate(counts.get("STALE_SUSPECT", 0), captured),
        "new_present_gap_count": gap_count,
        "new_present_gap_rate": _rate(gap_count, max(captured - 1, 1)),
        "freshness_counts": dict(counts),
        "note": (
            "no_new_present_rate and new_present_gap_rate are informational; "
            "they are not failures without an independent activity expectation"
        ),
    }
    t = SPIKE_THRESHOLDS["s0"]
    if captured == 0:
        result = GateResult.FAIL
    elif (
        captured >= t["pass_min_new_present_frames"]
        and metrics["black_rate"] <= t["pass_max_black_rate"]
        and metrics["stale_rate"] <= t["pass_max_stale_rate"]
        and metrics["capture_error_rate"] <= t["pass_max_capture_error_rate"]
    ):
        result = GateResult.PASS
    elif (
        metrics["black_rate"] <= t["degraded_max_black_rate"]
        and metrics["stale_rate"] <= t["degraded_max_stale_rate"]
        and metrics["capture_error_rate"] <= t["degraded_max_capture_error_rate"]
    ):
        result = GateResult.DEGRADED_SHIPPABLE
    else:
        result = GateResult.FAIL
    return {"result": result.value, "metrics": metrics, "thresholds": t}


def assess_s1(probe: dict | None, *, external_overlay_available: bool) -> dict:
    if probe is None:
        return {
            "result": GateResult.NOT_RUN.value,
            "reason": "inside-overlay contamination probe not run",
            "thresholds": SPIKE_THRESHOLDS["s1"],
        }

    t = SPIKE_THRESHOLDS["s1"]
    metrics = probe.get("metrics", {})
    conclusive = bool(probe.get("conclusive"))
    positive_control = (
        metrics.get("positive_control_signal_mean", -1.0)
        >= t["min_positive_control_signal_mean"]
        or metrics.get("positive_control_signal_p95", -1.0)
        >= t["min_positive_control_signal_p95"]
    )
    excluded_clean = (
        metrics.get("excluded_signal_mean", float("inf"))
        <= t["max_excluded_signal_mean"]
        and metrics.get("excluded_signal_p95", float("inf"))
        <= t["max_excluded_signal_p95"]
        and metrics.get("signal_reduction_mean", -1.0)
        >= t["min_signal_reduction_mean"]
    )
    affinity_ok = bool((probe.get("capture_exclusion") or {}).get("success"))
    exclusion_verified = bool(probe.get("exclusion_verified"))

    if conclusive and positive_control and exclusion_verified and affinity_ok and excluded_clean:
        result = GateResult.PASS
        reason = (
            "positive control proved overlay visibility with WDA_NONE, then "
            "WDA_EXCLUDEFROMCAPTURE removed the measured signal"
        )
    elif external_overlay_available:
        result = GateResult.DEGRADED_SHIPPABLE
        if not conclusive:
            reason = "S1 measurement was inconclusive; external panel remains the safe fallback"
        elif not positive_control:
            reason = "positive control did not prove overlay detectability; negative result cannot be trusted"
        else:
            reason = "inside overlay exclusion not cleanly verified; external panel remains the safe fallback"
    else:
        result = GateResult.FAIL
        reason = "inside overlay unresolved and no external fallback available"

    return {
        "result": result.value,
        "reason": reason,
        "metrics": metrics,
        "positive_control_passed": positive_control,
        "exclusion_verified": exclusion_verified,
        "thresholds": t,
    }


def summarize_registration(rows: list[dict]) -> dict:
    if not rows:
        return {"samples": 0}
    modes = Counter(row["mode"] for row in rows)
    accepted = sum(bool(row.get("accepted")) for row in rows)
    numeric = lambda key: [row[key] for row in rows if row.get(key) is not None]
    p95 = lambda values: float(np.percentile(values, 95)) if values else None
    return {
        "samples": len(rows),
        "mode_counts": dict(modes),
        "accepted_rate": accepted / len(rows),
        "failed_rate": modes.get("FAILED", 0) / len(rows),
        "scale_only_rate": modes.get("SCALE_ONLY", 0) / len(rows),
        "confidence_mean": mean(numeric("confidence")) if numeric("confidence") else None,
        "all_match_error_mean_p95": p95(numeric("all_match_error_mean")),
        "all_match_error_p90_p95": p95(numeric("all_match_error_p90")),
        "inlier_error_mean_p95": p95(numeric("inlier_error_mean")),
        "inliers_p05": float(np.percentile(numeric("inliers"), 5)) if numeric("inliers") else None,
    }


def assess_s2(rows: list[dict]) -> dict:
    summary = summarize_registration(rows)
    t = SPIKE_THRESHOLDS["s2"]
    if not rows:
        return {
            "result": GateResult.NOT_RUN.value,
            "summary": summary,
            "thresholds": t,
        }
    if (
        summary["accepted_rate"] >= t["pass_min_accepted_rate"]
        and summary["failed_rate"] <= t["pass_max_failed_rate"]
        and summary["scale_only_rate"] <= t["pass_max_scale_only_rate"]
    ):
        result = GateResult.PASS
    elif (
        summary["accepted_rate"] >= t["degraded_min_accepted_rate"]
        and summary["failed_rate"] <= t["degraded_max_failed_rate"]
    ):
        result = GateResult.DEGRADED_SHIPPABLE
    else:
        result = GateResult.FAIL
    return {"result": result.value, "summary": summary, "thresholds": t}
