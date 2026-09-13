from __future__ import annotations

from collections import Counter
from statistics import mean

import numpy as np

from .models import GateResult


SPIKE_THRESHOLDS = {
    "s0": {
        "pass_max_black_rate": 0.001,
        "pass_max_stale_rate": 0.01,
        "pass_max_none_grab_rate": 0.05,
        "pass_max_gap_rate": 0.05,
        "degraded_max_black_rate": 0.01,
        "degraded_max_stale_rate": 0.10,
        "degraded_max_none_grab_rate": 0.20,
        "degraded_max_gap_rate": 0.20,
    },
    "s1": {
        "max_overlay_signal_mean": 4.0,
        "max_overlay_signal_p95": 18.0,
        "max_hidden_baseline_mean": 6.0,
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


def assess_s0(*, health_rows: list[dict], none_grabs: int, gap_count: int) -> dict:
    total_attempts = len(health_rows) + none_grabs
    captured = len(health_rows)
    counts = Counter(row["freshness"] for row in health_rows)
    metrics = {
        "captured_frames": captured,
        "capture_attempts": total_attempts,
        "none_grab_rate": _rate(none_grabs, total_attempts),
        "black_rate": _rate(counts.get("BLACK", 0), captured),
        "stale_rate": _rate(counts.get("STALE_SUSPECT", 0), captured),
        "gap_rate": _rate(gap_count, max(captured - 1, 1)),
        "freshness_counts": dict(counts),
    }
    t = SPIKE_THRESHOLDS["s0"]
    if captured == 0:
        result = GateResult.FAIL
    elif (
        metrics["black_rate"] <= t["pass_max_black_rate"]
        and metrics["stale_rate"] <= t["pass_max_stale_rate"]
        and metrics["none_grab_rate"] <= t["pass_max_none_grab_rate"]
        and metrics["gap_rate"] <= t["pass_max_gap_rate"]
    ):
        result = GateResult.PASS
    elif (
        metrics["black_rate"] <= t["degraded_max_black_rate"]
        and metrics["stale_rate"] <= t["degraded_max_stale_rate"]
        and metrics["none_grab_rate"] <= t["degraded_max_none_grab_rate"]
        and metrics["gap_rate"] <= t["degraded_max_gap_rate"]
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
    clean = (
        conclusive
        and metrics.get("overlay_signal_mean", float("inf")) <= t["max_overlay_signal_mean"]
        and metrics.get("overlay_signal_p95", float("inf")) <= t["max_overlay_signal_p95"]
        and metrics.get("hidden_baseline_mean", float("inf")) <= t["max_hidden_baseline_mean"]
    )
    affinity_ok = bool((probe.get("capture_exclusion") or {}).get("success"))
    if clean and affinity_ok:
        result = GateResult.PASS
        reason = "inside overlay excluded from capture under a stable background"
    elif external_overlay_available:
        result = GateResult.DEGRADED_SHIPPABLE
        reason = "inside overlay not proven clean; external panel remains a shippable fallback"
    else:
        result = GateResult.FAIL
        reason = "overlay contamination unresolved and no external fallback available"
    return {"result": result.value, "reason": reason, "metrics": metrics, "thresholds": t}


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
