from __future__ import annotations

from collections import Counter
from statistics import mean

import numpy as np

from .models import GateResult


SPIKE_THRESHOLDS = {
    "s0": {
        # Measurement sufficiency, not a claim about how often a legal desktop must change.
        "pass_min_new_present_frames_per_minute": 12.0,
        "degraded_min_new_present_frames_per_minute": 2.0,
        "pass_max_black_rate": 0.001,
        "pass_max_stale_rate": 0.01,
        "pass_max_capture_error_rate": 0.01,
        "degraded_max_black_rate": 0.01,
        "degraded_max_stale_rate": 0.10,
        "degraded_max_capture_error_rate": 0.05,
    },
    "s1": {
        # S1 now detects a known probe signature (magenta border + cyan cross),
        # not generic image motion. The positive control must visibly contain the
        # signature. A clean exclusion should remove almost all of it.
        "min_positive_marker_score": 0.30,
        "max_excluded_marker_score": 0.10,
        "max_retained_fraction_for_working": 0.25,
        "min_retained_fraction_for_failure": 0.60,
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
    window_unavailable: int,
    gap_count: int,
    observed_seconds: float,
) -> dict:
    """Assess S0 without turning unmeasured liveness into a zero-valued KPI.

    DXcam returning None means no newly presented desktop frame, not a capture
    error. STALE_SUSPECT is only meaningful on frames where an independent
    activity witness explicitly said activity was expected. Until S3/S4 provide
    such a witness, stale_rate is null and S0 cannot receive a full PASS solely
    by observing zero stale events.
    """
    captured = len(health_rows)
    poll_attempts = captured + no_new_presents
    total_capture_operations = poll_attempts + capture_errors
    counts = Counter(row["freshness"] for row in health_rows)

    witnessed_rows = [row for row in health_rows if row.get("activity_expected") is True]
    stale_rate = (
        _rate(
            sum(row.get("freshness") == "STALE_SUSPECT" for row in witnessed_rows),
            len(witnessed_rows),
        )
        if witnessed_rows
        else None
    )
    stale_metric_status = (
        "MEASURED_WITH_ACTIVITY_WITNESS"
        if witnessed_rows
        else "NO_WITNESS_DEFERRED_TO_S3_S4"
    )

    minutes = max(float(observed_seconds), 0.001) / 60.0
    new_present_per_minute = captured / minutes
    t = SPIKE_THRESHOLDS["s0"]
    if new_present_per_minute >= t["pass_min_new_present_frames_per_minute"]:
        sample_sufficiency = "PASS_SAMPLE_DENSITY"
    elif new_present_per_minute >= t["degraded_min_new_present_frames_per_minute"]:
        sample_sufficiency = "DEGRADED_SAMPLE_DENSITY"
    else:
        sample_sufficiency = "LOW_SAMPLE_DENSITY"

    metrics = {
        "captured_new_present_frames": captured,
        "observed_seconds": float(observed_seconds),
        "new_present_frames_per_minute": new_present_per_minute,
        "sample_sufficiency": sample_sufficiency,
        "poll_attempts": poll_attempts,
        "no_new_present_count": no_new_presents,
        "no_new_present_rate": _rate(no_new_presents, poll_attempts),
        "window_unavailable_count": window_unavailable,
        "capture_error_count": capture_errors,
        "capture_error_rate": _rate(capture_errors, total_capture_operations),
        "black_rate": _rate(counts.get("BLACK", 0), captured),
        "stale_rate": stale_rate,
        "stale_metric_status": stale_metric_status,
        "activity_witnessed_frames": len(witnessed_rows),
        "new_present_gap_count": gap_count,
        "new_present_gap_rate": _rate(gap_count, max(captured - 1, 1)),
        "freshness_counts": dict(counts),
        "note": (
            "no_new_present/gap/window_unavailable are separate evidence classes; "
            "stale_rate is null until an independent activity witness exists"
        ),
    }

    stale_pass = stale_rate is not None and stale_rate <= t["pass_max_stale_rate"]
    stale_degraded_ok = stale_rate is None or stale_rate <= t["degraded_max_stale_rate"]
    hard_health_pass = (
        metrics["black_rate"] <= t["pass_max_black_rate"]
        and metrics["capture_error_rate"] <= t["pass_max_capture_error_rate"]
    )
    degraded_health_ok = (
        metrics["black_rate"] <= t["degraded_max_black_rate"]
        and metrics["capture_error_rate"] <= t["degraded_max_capture_error_rate"]
        and stale_degraded_ok
    )

    if captured == 0:
        result = GateResult.FAIL
        reason = "no new-present frame was captured"
    elif (
        hard_health_pass
        and stale_pass
        and sample_sufficiency == "PASS_SAMPLE_DENSITY"
    ):
        result = GateResult.PASS
        reason = "capture health and witnessed liveness both met PASS thresholds"
    elif degraded_health_ok:
        result = GateResult.DEGRADED_SHIPPABLE
        if stale_rate is None:
            reason = "capture path is usable, but stale/freeze detection is unmeasured until S3/S4 provide a liveness witness"
        elif sample_sufficiency != "PASS_SAMPLE_DENSITY":
            reason = "capture path is usable, but new-present sample density is below PASS evidence threshold"
        else:
            reason = "capture path is usable under degraded health thresholds"
    else:
        result = GateResult.FAIL
        reason = "capture health exceeded degraded thresholds"

    return {
        "result": result.value,
        "reason": reason,
        "metrics": metrics,
        "thresholds": t,
    }


def assess_s1(probe: dict | None, *, external_overlay_available: bool) -> dict:
    if probe is None:
        return {
            "result": GateResult.NOT_RUN.value,
            "reason": "inside-overlay contamination probe not run",
            "exclusion_outcome": "UNMEASURED",
            "verdict_basis": "MARKER_SIGNATURE_PRESENCE",
            "thresholds": SPIKE_THRESHOLDS["s1"],
        }

    t = SPIKE_THRESHOLDS["s1"]
    metrics = probe.get("metrics", {})
    conclusive = bool(probe.get("conclusive"))
    positive_score = metrics.get("positive_marker_score")
    excluded_score = metrics.get("excluded_marker_score")
    retained_fraction = metrics.get("marker_retained_fraction")

    positive_control = (
        positive_score is not None
        and positive_score >= t["min_positive_marker_score"]
    )
    affinity_ok = bool((probe.get("capture_exclusion") or {}).get("success"))

    if not conclusive or not positive_control:
        exclusion_outcome = "UNMEASURED"
    elif not affinity_ok:
        exclusion_outcome = "PROVEN_NOT_WORKING"
    elif excluded_score is None or retained_fraction is None:
        exclusion_outcome = "UNMEASURED"
    elif (
        excluded_score <= t["max_excluded_marker_score"]
        and retained_fraction <= t["max_retained_fraction_for_working"]
    ):
        exclusion_outcome = "PROVEN_WORKING"
    elif retained_fraction >= t["min_retained_fraction_for_failure"]:
        exclusion_outcome = "PROVEN_NOT_WORKING"
    else:
        # Between clean-removal and clear-retention thresholds, preserve the
        # uncertainty. Ambiguous signature evidence must never be promoted to a
        # product conclusion merely because generic game motion changed.
        exclusion_outcome = "UNMEASURED"

    if exclusion_outcome == "PROVEN_WORKING":
        result = GateResult.PASS
        reason = (
            "positive control detected the known probe signature and enabling "
            "WDA_EXCLUDEFROMCAPTURE removed that signature"
        )
    elif external_overlay_available:
        result = GateResult.DEGRADED_SHIPPABLE
        if exclusion_outcome == "PROVEN_NOT_WORKING":
            reason = "the known probe signature remained after exclusion; external panel remains the safe fallback"
        elif not conclusive:
            reason = "S1 measurement was inconclusive; external panel remains the safe fallback"
        elif not positive_control:
            reason = "positive control did not detect the known probe signature; negative result cannot be trusted"
        else:
            reason = "marker-signature evidence was ambiguous; external panel remains the safe fallback"
    else:
        result = GateResult.FAIL
        reason = "inside overlay unresolved and no external fallback available"

    return {
        "result": result.value,
        "reason": reason,
        "metrics": metrics,
        "positive_control_passed": positive_control,
        "exclusion_outcome": exclusion_outcome,
        "verdict_basis": "MARKER_SIGNATURE_PRESENCE",
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
