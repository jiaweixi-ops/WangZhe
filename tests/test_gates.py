from qijing_spike.gates import assess_s0


def health_rows(n=5, freshness="QUIET", *, activity_expected=None):
    return [
        {"freshness": freshness, "activity_expected": activity_expected}
        for _ in range(n)
    ]


def test_no_witness_reports_null_stale_metric_instead_of_zero():
    gate = assess_s0(
        health_rows=health_rows(5),
        no_new_presents=500,
        capture_errors=0,
        window_unavailable=0,
        gap_count=20,
        observed_seconds=10.0,
    )
    assert gate["result"] == "DEGRADED_SHIPPABLE"
    assert gate["metrics"]["stale_rate"] is None
    assert gate["metrics"]["stale_metric_status"] == "NO_WITNESS_DEFERRED_TO_S3_S4"
    assert gate["metrics"]["capture_error_rate"] == 0.0


def test_witnessed_healthy_stream_can_pass():
    gate = assess_s0(
        health_rows=health_rows(5, activity_expected=True),
        no_new_presents=20,
        capture_errors=0,
        window_unavailable=0,
        gap_count=0,
        observed_seconds=10.0,
    )
    assert gate["result"] == "PASS"
    assert gate["metrics"]["stale_rate"] == 0.0
    assert gate["metrics"]["new_present_frames_per_minute"] == 30.0


def test_low_new_present_density_cannot_receive_full_pass():
    gate = assess_s0(
        health_rows=health_rows(5, activity_expected=True),
        no_new_presents=100,
        capture_errors=0,
        window_unavailable=0,
        gap_count=0,
        observed_seconds=1800.0,
    )
    assert gate["metrics"]["new_present_frames_per_minute"] < 1.0
    assert gate["result"] == "DEGRADED_SHIPPABLE"


def test_window_unavailable_is_not_a_capture_error():
    gate = assess_s0(
        health_rows=health_rows(5, activity_expected=True),
        no_new_presents=0,
        capture_errors=0,
        window_unavailable=50,
        gap_count=0,
        observed_seconds=10.0,
    )
    assert gate["metrics"]["window_unavailable_count"] == 50
    assert gate["metrics"]["capture_error_rate"] == 0.0
    assert gate["result"] == "PASS"


def test_real_capture_errors_can_fail_s0():
    gate = assess_s0(
        health_rows=health_rows(5, activity_expected=True),
        no_new_presents=0,
        capture_errors=10,
        window_unavailable=0,
        gap_count=0,
        observed_seconds=10.0,
    )
    assert gate["result"] == "FAIL"
