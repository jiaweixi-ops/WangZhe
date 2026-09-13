from qijing_spike.gates import assess_s0, assess_s3, assess_s4


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


def test_no_present_under_s3_s4_witness_can_fail_liveness():
    liveness = [
        {
            "activity_expected": True,
            "new_present": False,
            "stale_suspect": True,
        }
        for _ in range(5)
    ]
    gate = assess_s0(
        health_rows=health_rows(5, activity_expected=True),
        no_new_presents=5,
        capture_errors=0,
        window_unavailable=0,
        gap_count=0,
        observed_seconds=10.0,
        liveness_rows=liveness,
    )
    assert gate["metrics"]["stale_metric_status"] == "MEASURED_WITH_S3_S4_ACTIVITY_WITNESS"
    assert gate["metrics"]["stale_no_present_count"] == 5
    assert gate["metrics"]["stale_rate"] == 1.0
    assert gate["result"] == "FAIL"


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


def test_s3_controlled_preparation_run_can_pass():
    rows = []
    for _ in range(30):
        rows.append({"stable_phase": "PREPARATION"})
    gate = assess_s3(rows, expected_phase="PREPARATION")
    assert gate["result"] == "PASS"
    assert gate["metrics"]["agreement"] == 1.0


def test_s3_without_ground_truth_cannot_full_pass():
    rows = [{"stable_phase": "PREPARATION"} for _ in range(20)]
    gate = assess_s3(rows)
    assert gate["result"] == "DEGRADED_SHIPPABLE"


def test_s4_controlled_preparation_timer_can_pass():
    rows = [
        {
            "stable_phase": "PREPARATION",
            "valid": True,
            "monotonic_ok": True,
            "activity_expected": True,
            "source": "FUSED",
        }
        for _ in range(20)
    ]
    gate = assess_s4(rows, expected_phase="PREPARATION")
    assert gate["result"] == "PASS"
    assert gate["metrics"]["valid_timer_rate"] == 1.0
    assert gate["metrics"]["activity_witness_rate"] == 1.0


def test_s4_without_controlled_ground_truth_is_degraded():
    rows = [
        {
            "stable_phase": "PREPARATION",
            "valid": True,
            "monotonic_ok": True,
            "activity_expected": True,
            "source": "DIGIT",
        }
        for _ in range(20)
    ]
    gate = assess_s4(rows)
    assert gate["result"] == "DEGRADED_SHIPPABLE"
