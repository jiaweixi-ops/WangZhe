from qijing_spike.gates import assess_s0


def health_rows(n=5, freshness="QUIET"):
    return [{"freshness": freshness} for _ in range(n)]


def test_no_new_present_polls_are_not_capture_failures():
    gate = assess_s0(
        health_rows=health_rows(5),
        no_new_presents=500,
        capture_errors=0,
        gap_count=20,
    )
    assert gate["result"] == "PASS"
    assert gate["metrics"]["no_new_present_rate"] > 0.98
    assert gate["metrics"]["capture_error_rate"] == 0.0


def test_real_capture_errors_can_fail_s0():
    gate = assess_s0(
        health_rows=health_rows(5),
        no_new_presents=0,
        capture_errors=10,
        gap_count=0,
    )
    assert gate["result"] == "FAIL"
