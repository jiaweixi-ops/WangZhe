from pathlib import Path

import cv2
import numpy as np

from qijing_spike.phase import GamePhase
from qijing_spike.stage_profile import ArcTimerConfig, DigitTimerConfig, TimerConfig
from qijing_spike.timer import ArcTimerReader, DigitTemplateTimerReader, TimerTracker


def _draw_digit(digit: int, *, width=42, height=58) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        image,
        str(digit),
        (4, 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.55,
        (255, 255, 255),
        3,
        cv2.LINE_AA,
    )
    return image


def _write_templates(tmp_path: Path) -> Path:
    root = tmp_path / "digits"
    root.mkdir()
    for digit in range(10):
        cv2.imwrite(str(root / f"{digit}.png"), _draw_digit(digit))
    return root


def _timer_image(value: str) -> np.ndarray:
    canvas = np.zeros((64, max(50, len(value) * 44), 3), dtype=np.uint8)
    for index, char in enumerate(value):
        glyph = _draw_digit(int(char), width=42, height=58)
        x0 = index * 44
        canvas[3:61, x0 : x0 + 42] = glyph
    return canvas


def test_digit_template_timer_reads_seconds(tmp_path):
    config = DigitTimerConfig(_write_templates(tmp_path), min_digit_score=0.45, max_digits=3)
    reader = DigitTemplateTimerReader(config)
    reading = reader.read(_timer_image("12"))
    assert reading.valid is True
    assert reading.seconds == 12.0
    assert reading.confidence >= 0.45


def test_arc_timer_estimates_half_remaining():
    image = np.zeros((120, 120, 3), dtype=np.uint8)
    # Bright green is HSV ~60,255,255. Draw half of the configured ring.
    cv2.ellipse(image, (60, 60), (42, 42), 0, -90, 90, (0, 255, 0), 7)
    config = ArcTimerConfig(
        center=(0.5, 0.5),
        radius=0.35,
        thickness=0.06,
        start_angle_deg=-90,
        sweep_angle_deg=360,
        clockwise=False,
        hsv_min=(50, 180, 180),
        hsv_max=(70, 255, 255),
        max_seconds=30.0,
        active_represents_remaining=True,
        angular_samples=180,
    )
    reading = ArcTimerReader(config).read(image)
    assert reading.valid is True
    assert 11.0 <= reading.seconds <= 19.0
    assert reading.confidence > 0.5


def test_timer_tracker_creates_witness_after_consistent_preparation_reads(tmp_path):
    digit = DigitTimerConfig(_write_templates(tmp_path), min_digit_score=0.45, max_digits=3)
    config = TimerConfig(
        roi=(0.0, 0.0, 1.0, 1.0),
        min_confidence=0.45,
        disagreement_seconds=1.5,
        witness_min_confidence=0.45,
        witness_min_streak=2,
        digit=digit,
        arc=None,
    )
    tracker = TimerTracker(config)
    image = _timer_image("12")

    first = tracker.observe(image, 1_000_000_000, GamePhase.PREPARATION)
    witness1 = tracker.activity_witness(first, GamePhase.PREPARATION)
    assert first.valid is True
    assert witness1.expected is None

    second = tracker.observe(image, 1_200_000_000, GamePhase.PREPARATION)
    witness2 = tracker.activity_witness(second, GamePhase.PREPARATION)
    assert second.valid_streak == 2
    assert witness2.expected is True
    assert witness2.source == "PREPARATION_TIMER"


def test_timer_tracker_does_not_assert_liveness_outside_preparation(tmp_path):
    digit = DigitTimerConfig(_write_templates(tmp_path), min_digit_score=0.45, max_digits=3)
    config = TimerConfig(
        roi=(0.0, 0.0, 1.0, 1.0),
        min_confidence=0.45,
        witness_min_confidence=0.45,
        witness_min_streak=1,
        digit=digit,
        arc=None,
    )
    tracker = TimerTracker(config)
    reading = tracker.observe(_timer_image("12"), 1_000_000_000, GamePhase.COMBAT)
    witness = tracker.activity_witness(reading, GamePhase.COMBAT)
    assert reading.valid is False
    assert witness.expected is None
