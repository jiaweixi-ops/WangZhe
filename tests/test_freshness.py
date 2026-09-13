import numpy as np

from qijing_spike.freshness import FrameHealthMonitor
from qijing_spike.models import CapturedFrame, FrameFreshness, Rect


def frame(image, idx, seconds=None):
    t = idx if seconds is None else seconds
    return CapturedFrame(
        frame_id=idx,
        sequence_id=idx,
        capture_timestamp_ns=int(t * 1_000_000_000),
        image_bgr=image.copy(),
        region=Rect(0, 0, image.shape[1], image.shape[0]),
        backend="test",
    )


def test_black_frame_detected():
    monitor = FrameHealthMonitor()
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    health = monitor.observe(frame(image, 1, 0.0))
    assert health.freshness == FrameFreshness.BLACK


def test_small_local_change_is_detected():
    monitor = FrameHealthMonitor()
    base = np.full((216, 384, 3), 80, dtype=np.uint8)
    monitor.observe(frame(base, 1, 0.0))
    changed = base.copy()
    changed[20:32, 40:52] = 240
    health = monitor.observe(frame(changed, 2, 1.0))
    assert health.freshness == FrameFreshness.FRESH
    assert health.changed_tiles >= 1


def test_static_scene_does_not_become_stale_without_activity_baseline():
    monitor = FrameHealthMonitor(stale_after_seconds=2.0)
    image = np.full((216, 384, 3), 90, dtype=np.uint8)
    monitor.observe(frame(image, 1, 0.0))
    health = monitor.observe(frame(image, 2, 5.0))
    assert health.freshness == FrameFreshness.QUIET
    assert not health.learned_dynamic


def test_dynamic_stream_then_freeze_becomes_stale():
    monitor = FrameHealthMonitor(
        min_activity_events=2,
        learning_window_seconds=10.0,
        stale_after_seconds=2.0,
    )
    base = np.full((216, 384, 3), 90, dtype=np.uint8)
    monitor.observe(frame(base, 1, 0.0))

    f1 = base.copy()
    f1[20:35, 40:55] = 180
    monitor.observe(frame(f1, 2, 1.0))

    f2 = base.copy()
    f2[20:35, 55:70] = 180
    active = monitor.observe(frame(f2, 3, 2.0))
    assert active.learned_dynamic

    health = monitor.observe(frame(f2, 4, 4.5))
    assert health.freshness == FrameFreshness.STALE_SUSPECT
    assert health.seconds_since_meaningful_change >= 2.0
