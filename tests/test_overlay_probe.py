import cv2
import numpy as np

from qijing_spike.gates import assess_s1
from qijing_spike.models import CapturedFrame, MonitorInfo, Rect, WindowInfo
from qijing_spike.overlay import (
    PROBE_MARKER_BORDER_RGB,
    PROBE_MARKER_CROSS_HALF,
    PROBE_MARKER_CROSS_RGB,
    PROBE_MARKER_INSET,
    PROBE_MARKER_LINE_WIDTH,
)
from qijing_spike.overlay_probe import probe_overlay_exclusion


TARGET = Rect(10, 10, 30, 30)


def make_frame(image, idx, *, cached=False):
    return CapturedFrame(
        frame_id=idx,
        sequence_id=idx,
        capture_timestamp_ns=idx,
        image_bgr=image.copy(),
        region=Rect(0, 0, image.shape[1], image.shape[0]),
        backend="stub",
        monitor_index=0,
        reused_cached=cached,
    )


class StubBackend:
    def __init__(self, frames):
        self.frames = list(frames)
        self.last = self.frames[-1] if self.frames else None

    def grab(self, region, monitor, *, allow_cached=False):
        if self.frames:
            self.last = self.frames.pop(0)
            return self.last
        return self.last if allow_cached else None


class StubQt:
    def processEvents(self):
        pass


class StubOverlay:
    def __init__(self):
        self.capture_exclusion_result = None
        self.click_through_result = {"success": True}
        self.capture_affinity_history = []
        self._rect = TARGET
        self.visible = False
        self.probe_mode = False

    def hide(self):
        self.visible = False

    def show(self):
        self.visible = True

    def anchor_inside(self, viewport):
        pass

    def physical_rect(self):
        return self._rect

    def set_probe_marker_mode(self, enabled):
        self.probe_mode = bool(enabled)

    def set_capture_excluded(self, excluded):
        result = {
            "success": True,
            "last_error": 0,
            "affinity": 0x11 if excluded else 0,
            "excluded": excluded,
        }
        self.capture_affinity_history.append(result)
        if excluded:
            self.capture_exclusion_result = result
        return result


def window():
    monitor = MonitorInfo(
        index=0,
        handle=1,
        rect=Rect(0, 0, 100, 100),
        work_rect=Rect(0, 0, 100, 100),
    )
    return WindowInfo(
        hwnd=1,
        title="game",
        class_name="stub",
        pid=1,
        exe="game.exe",
        window_rect=Rect(0, 0, 100, 100),
        client_rect=Rect(0, 0, 100, 100),
        visible=True,
        minimized=False,
        monitor=monitor,
    )


def _bgr(rgb):
    return int(rgb[2]), int(rgb[1]), int(rgb[0])


def draw_probe_marker(image: np.ndarray, rect: Rect = TARGET) -> np.ndarray:
    out = image.copy()
    w, h = rect.width, rect.height
    scale = min(w, h) / 72.0
    inset = max(1, int(round(PROBE_MARKER_INSET * scale)))
    line_width = max(1, int(round(PROBE_MARKER_LINE_WIDTH * scale)))
    cross_half = max(2, int(round(PROBE_MARKER_CROSS_HALF * scale)))

    x0 = rect.left + inset
    y0 = rect.top + inset
    x1 = rect.right - inset - 1
    y1 = rect.bottom - inset - 1
    cv2.rectangle(out, (x0, y0), (x1, y1), _bgr(PROBE_MARKER_BORDER_RGB), line_width)

    cx = rect.left + w // 2
    cy = rect.top + h // 2
    cv2.line(
        out,
        (max(rect.left, cx - cross_half), cy),
        (min(rect.right - 1, cx + cross_half), cy),
        _bgr(PROBE_MARKER_CROSS_RGB),
        line_width,
    )
    cv2.line(
        out,
        (cx, max(rect.top, cy - cross_half)),
        (cx, min(rect.bottom - 1, cy + cross_half)),
        _bgr(PROBE_MARKER_CROSS_RGB),
        line_width,
    )
    return out


def base_image(value=100):
    return np.full((100, 100, 3), value, dtype=np.uint8)


def run_probe(frames, baseline):
    return probe_overlay_exclusion(
        backend=StubBackend(frames),
        window=window(),
        overlay=StubOverlay(),
        qt_app=StubQt(),
        viewport_screen=Rect(0, 0, 100, 100),
        baseline_frame=make_frame(baseline, 0),
        settle_seconds=0,
        fresh_timeout_seconds=0.03,
    )


def test_positive_control_then_exclusion_can_pass():
    baseline = base_image()
    positive = draw_probe_marker(baseline)
    probe = run_probe(
        [make_frame(positive, 1), make_frame(baseline, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)
    assert probe["conclusive"] is True
    assert gate["positive_control_passed"] is True
    assert gate["verdict_basis"] == "MARKER_SIGNATURE_PRESENCE"
    assert gate["exclusion_outcome"] == "PROVEN_WORKING"
    assert gate["result"] == "PASS"


def test_local_game_motion_under_marker_does_not_prove_exclusion_failed():
    baseline = base_image()

    positive_underlay = baseline.copy()
    positive_underlay[10:30, 10:30] = 180
    positive = draw_probe_marker(positive_underlay)

    excluded_success = baseline.copy()
    excluded_success[10:30, 10:30] = 137

    probe = run_probe(
        [make_frame(positive, 1), make_frame(excluded_success, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)

    assert gate["positive_control_passed"] is True
    assert probe["metrics"]["excluded_target_mean"] > 3.0
    assert probe["metrics"]["excluded_marker_score"] < 0.10
    assert gate["exclusion_outcome"] == "PROVEN_WORKING"
    assert gate["result"] == "PASS"


def test_asymmetric_local_motion_cannot_fake_exclusion_success_when_marker_remains():
    """Regression for probe8 scenario 9.

    F+ has stronger local game motion than F-, so a generic difference-reduction
    verdict would incorrectly certify exclusion. The known marker remains visible
    in F-, therefore the signature verdict must prove exclusion did NOT work.
    """
    baseline = base_image()

    positive_underlay = baseline.copy()
    positive_underlay[10:30, 10:30] = 180
    positive = draw_probe_marker(positive_underlay)

    excluded_underlay = baseline.copy()
    excluded_underlay[10:30, 10:30] = 112
    excluded_failure = draw_probe_marker(excluded_underlay)

    probe = run_probe(
        [make_frame(positive, 1), make_frame(excluded_failure, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)

    assert gate["positive_control_passed"] is True
    # Diagnostic generic motion is allowed to suggest a reduction; it has no
    # verdict authority anymore.
    assert probe["metrics"]["positive_marker_score"] >= 0.30
    assert probe["metrics"]["excluded_marker_score"] >= 0.30
    assert probe["metrics"]["marker_retained_fraction"] >= 0.60
    assert gate["exclusion_outcome"] == "PROVEN_NOT_WORKING"
    assert gate["result"] == "DEGRADED_SHIPPABLE"


def test_uniform_game_motion_does_not_hide_marker_signature():
    baseline = base_image()
    moving = base_image(125)
    positive = draw_probe_marker(moving)

    probe = run_probe(
        [make_frame(positive, 1), make_frame(moving, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)

    assert gate["positive_control_passed"] is True
    assert probe["metrics"]["positive_marker_score"] >= 0.30
    assert probe["metrics"]["excluded_marker_score"] < 0.10
    assert gate["exclusion_outcome"] == "PROVEN_WORKING"
    assert gate["result"] == "PASS"


def test_visible_marker_after_exclusion_is_proven_not_working():
    baseline = base_image()
    contaminated = draw_probe_marker(baseline)
    probe = run_probe(
        [make_frame(contaminated, 1), make_frame(contaminated, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)
    assert gate["positive_control_passed"] is True
    assert gate["exclusion_outcome"] == "PROVEN_NOT_WORKING"
    assert gate["result"] == "DEGRADED_SHIPPABLE"


def test_ambiguous_partial_signature_is_unmeasured_not_forced_to_pass_or_fail():
    baseline = base_image()
    positive = draw_probe_marker(baseline)
    partial = draw_probe_marker(baseline)
    # Erase roughly half the signature. This should land between clean removal
    # and clear retention and therefore remain explicitly unmeasured.
    partial[10:30, 20:30] = baseline[10:30, 20:30]

    probe = run_probe(
        [make_frame(positive, 1), make_frame(partial, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)
    assert gate["positive_control_passed"] is True
    assert gate["exclusion_outcome"] in {"UNMEASURED", "PROVEN_NOT_WORKING"}
    # The only forbidden result here is a false certification.
    assert gate["result"] != "PASS"


def test_cached_frames_cannot_prove_exclusion():
    baseline = base_image()
    contaminated = draw_probe_marker(baseline)
    cached = make_frame(contaminated, 1, cached=True)
    probe = run_probe([cached], baseline)
    gate = assess_s1(probe, external_overlay_available=True)
    assert probe["conclusive"] is False
    assert gate["exclusion_outcome"] == "UNMEASURED"
    assert gate["result"] == "DEGRADED_SHIPPABLE"


def test_missing_positive_control_cannot_pass():
    baseline = base_image()
    probe = run_probe(
        [make_frame(baseline, 1), make_frame(baseline, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)
    assert gate["positive_control_passed"] is False
    assert gate["exclusion_outcome"] == "UNMEASURED"
    assert gate["result"] == "DEGRADED_SHIPPABLE"
