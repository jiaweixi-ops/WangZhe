import numpy as np

from qijing_spike.freshness import FrameHealthMonitor
from qijing_spike.models import CapturedFrame, FrameFreshness, Rect


def frame(image, idx):
    return CapturedFrame(
        frame_id=idx,
        sequence_id=idx,
        capture_timestamp_ns=idx,
        image_bgr=image,
        region=Rect(0, 0, image.shape[1], image.shape[0]),
        backend="test",
    )


def test_black_frame_detected():
    monitor = FrameHealthMonitor()
    image = np.zeros((50, 50, 3), dtype=np.uint8)
    health = monitor.observe(frame(image, 1), expected_change=False)
    assert health.freshness == FrameFreshness.BLACK


def test_repeated_frame_becomes_stale_when_change_expected():
    monitor = FrameHealthMonitor(stale_repeat_threshold=2)
    image = np.full((50, 50, 3), 90, dtype=np.uint8)
    monitor.observe(frame(image, 1), expected_change=True)
    monitor.observe(frame(image, 2), expected_change=True)
    health = monitor.observe(frame(image, 3), expected_change=True)
    assert health.freshness == FrameFreshness.STALE_SUSPECT
