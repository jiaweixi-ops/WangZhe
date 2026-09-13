import numpy as np

from qijing_spike.gates import assess_s1
from qijing_spike.models import CapturedFrame, MonitorInfo, Rect, WindowInfo
from qijing_spike.overlay_probe import probe_overlay_exclusion


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
        self._rect = Rect(10, 10, 30, 30)
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


def images(*, moving=False):
    baseline = np.full((100, 100, 3), 100, dtype=np.uint8)

    # The target is 10:30,10:30. The first same-size control block selected by
    # the probe is 10:30,42:62 (screen x=42:62, y=10:30).
    motion = baseline.copy()
    if moving:
        motion[10:30, 10:30] = 125
        motion[10:30, 42:62] = 125

    contaminated = motion.copy()
    contaminated[10:30, 10:30] = 180
    return baseline, motion, contaminated


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
    baseline, _, contaminated = images()
    probe = run_probe(
        [make_frame(contaminated, 1), make_frame(baseline, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)
    assert probe["conclusive"] is True
    assert gate["positive_control_passed"] is True
    assert gate["exclusion_outcome"] == "PROVEN_WORKING"
    assert gate["result"] == "PASS"


def test_equal_game_motion_under_target_and_control_is_normalized_out():
    baseline, motion, contaminated = images(moving=True)
    probe = run_probe(
        [make_frame(contaminated, 1), make_frame(motion, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)
    assert gate["positive_control_passed"] is True
    assert probe["metrics"]["positive_control_motion_mean"] > 0
    assert probe["metrics"]["excluded_target_mean"] > 0
    assert probe["metrics"]["excluded_signal_mean"] == 0.0
    assert gate["exclusion_outcome"] == "PROVEN_WORKING"
    assert gate["result"] == "PASS"


def test_visible_marker_after_exclusion_is_proven_not_working():
    baseline, motion, contaminated = images(moving=True)
    contaminated_after_exclusion = contaminated.copy()
    probe = run_probe(
        [make_frame(contaminated, 1), make_frame(contaminated_after_exclusion, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)
    assert gate["positive_control_passed"] is True
    assert gate["exclusion_outcome"] == "PROVEN_NOT_WORKING"
    assert gate["result"] == "DEGRADED_SHIPPABLE"


def test_cached_frames_cannot_prove_exclusion():
    baseline, _, contaminated = images()
    cached = make_frame(contaminated, 1, cached=True)
    probe = run_probe([cached], baseline)
    gate = assess_s1(probe, external_overlay_available=True)
    assert probe["conclusive"] is False
    assert gate["exclusion_outcome"] == "UNMEASURED"
    assert gate["result"] == "DEGRADED_SHIPPABLE"


def test_missing_positive_control_cannot_pass():
    baseline, motion, _ = images(moving=True)
    probe = run_probe(
        [make_frame(motion, 1), make_frame(motion, 2)],
        baseline,
    )
    gate = assess_s1(probe, external_overlay_available=True)
    assert gate["positive_control_passed"] is False
    assert gate["exclusion_outcome"] == "UNMEASURED"
    assert gate["result"] == "DEGRADED_SHIPPABLE"
