from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from .phase import GamePhase
from .stage_profile import ArcTimerConfig, DigitTimerConfig, TimerConfig


def _crop_normalized(image: np.ndarray, roi: tuple[float, float, float, float]) -> np.ndarray:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = roi
    left = max(0, min(w - 1, int(round(x0 * w))))
    top = max(0, min(h - 1, int(round(y0 * h))))
    right = max(left + 1, min(w, int(round(x1 * w))))
    bottom = max(top + 1, min(h, int(round(y1 * h))))
    return image[top:bottom, left:right]


def _foreground_binary(image: np.ndarray) -> np.ndarray:
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, a = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    b = cv2.bitwise_not(a)

    # Timer glyphs should occupy a minority of the ROI. Pick the polarity with
    # the smaller nonzero fraction while avoiding a degenerate empty mask.
    candidates = []
    for binary in (a, b):
        fraction = float((binary > 0).mean())
        if 0.005 <= fraction <= 0.65:
            candidates.append((fraction, binary))
    if not candidates:
        return a
    return min(candidates, key=lambda item: item[0])[1]


def _tight_bbox(binary: np.ndarray) -> np.ndarray | None:
    ys, xs = np.where(binary > 0)
    if len(xs) == 0 or len(ys) == 0:
        return None
    return binary[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _normalize_glyph(binary: np.ndarray, size: tuple[int, int] = (24, 40)) -> np.ndarray | None:
    tight = _tight_bbox(binary)
    if tight is None or tight.size == 0:
        return None
    target_w, target_h = size
    h, w = tight.shape[:2]
    scale = min((target_w - 4) / max(w, 1), (target_h - 4) / max(h, 1))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(tight, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((target_h, target_w), dtype=np.uint8)
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas[y : y + new_h, x : x + new_w] = resized
    return (canvas > 0).astype(np.uint8)


def _glyph_iou(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    av = a > 0
    bv = b > 0
    union = np.logical_or(av, bv).sum()
    if union == 0:
        return 0.0
    return float(np.logical_and(av, bv).sum() / union)


@dataclass(frozen=True)
class TimerChannelReading:
    source: str
    seconds: float | None
    confidence: float
    valid: bool
    reason: str
    details: dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TimerReading:
    seconds: float | None
    confidence: float
    source: str
    valid: bool
    valid_streak: int
    monotonic_ok: bool
    reason: str
    channels: dict[str, dict]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ActivityWitness:
    expected: bool | None
    source: str
    confidence: float
    valid_for_seconds: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class DigitTemplateTimerReader:
    """Small OCR channel using only user-supplied digit templates 0.png..9.png."""

    def __init__(self, config: DigitTimerConfig) -> None:
        self.config = config
        self.templates: dict[int, np.ndarray] = {}
        for digit in range(10):
            path = config.templates_dir / f"{digit}.png"
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise RuntimeError(f"missing digit template: {path}")
            normalized = _normalize_glyph(_foreground_binary(image))
            if normalized is None:
                raise RuntimeError(f"digit template contains no foreground: {path}")
            self.templates[digit] = normalized

    def _components(self, roi_bgr: np.ndarray) -> list[np.ndarray]:
        binary = _foreground_binary(roi_bgr)
        n, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        h, w = binary.shape[:2]
        components: list[tuple[int, np.ndarray]] = []
        for label in range(1, n):
            x, y, cw, ch, area = stats[label]
            if area < max(6, int(binary.size * 0.0015)):
                continue
            if ch < max(5, int(h * 0.28)):
                continue
            if cw > int(w * 0.75) or ch > int(h * 0.98):
                continue
            glyph = binary[y : y + ch, x : x + cw]
            normalized = _normalize_glyph(glyph)
            if normalized is not None:
                components.append((x, normalized))
        components.sort(key=lambda item: item[0])
        return [glyph for _, glyph in components[: self.config.max_digits]]

    def read(self, timer_roi_bgr: np.ndarray) -> TimerChannelReading:
        glyphs = self._components(timer_roi_bgr)
        if not glyphs:
            return TimerChannelReading(
                source="DIGIT",
                seconds=None,
                confidence=0.0,
                valid=False,
                reason="no timer digit components found",
                details={"digits": []},
            )

        digits: list[int] = []
        scores: list[float] = []
        for glyph in glyphs:
            ranked = sorted(
                ((digit, _glyph_iou(glyph, template)) for digit, template in self.templates.items()),
                key=lambda item: item[1],
                reverse=True,
            )
            digit, score = ranked[0]
            digits.append(digit)
            scores.append(score)

        confidence = float(np.mean(scores)) if scores else 0.0
        if any(score < self.config.min_digit_score for score in scores):
            return TimerChannelReading(
                source="DIGIT",
                seconds=None,
                confidence=confidence,
                valid=False,
                reason="one or more digit matches below configured score",
                details={"digits": digits, "scores": scores},
            )

        value = int("".join(str(digit) for digit in digits))
        return TimerChannelReading(
            source="DIGIT",
            seconds=float(value),
            confidence=confidence,
            valid=True,
            reason="digit templates matched",
            details={"digits": digits, "scores": scores},
        )


class ArcTimerReader:
    """Color-calibrated annular progress reader for a preparation countdown arc."""

    def __init__(self, config: ArcTimerConfig) -> None:
        self.config = config

    def read(self, timer_roi_bgr: np.ndarray) -> TimerChannelReading:
        if timer_roi_bgr.size == 0:
            return TimerChannelReading("ARC", None, 0.0, False, "empty timer ROI", {})

        hsv = cv2.cvtColor(timer_roi_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array(self.config.hsv_min, dtype=np.uint8),
            np.array(self.config.hsv_max, dtype=np.uint8),
        )
        h, w = mask.shape[:2]
        cx = self.config.center[0] * (w - 1)
        cy = self.config.center[1] * (h - 1)
        base_radius = self.config.radius * min(w, h)
        half_band = max(1.0, self.config.thickness * min(w, h) / 2.0)
        sample_count = max(24, self.config.angular_samples)
        direction = -1.0 if self.config.clockwise else 1.0
        angles = self.config.start_angle_deg + direction * np.linspace(
            0.0,
            self.config.sweep_angle_deg,
            sample_count,
            endpoint=False,
        )

        radial_offsets = np.linspace(-half_band, half_band, 5)
        active_votes: list[float] = []
        for angle_deg in angles:
            angle = np.deg2rad(angle_deg)
            values = []
            for offset in radial_offsets:
                radius = max(1.0, base_radius + offset)
                x = int(round(cx + radius * np.cos(angle)))
                y = int(round(cy + radius * np.sin(angle)))
                if 0 <= x < w and 0 <= y < h:
                    values.append(1.0 if mask[y, x] > 0 else 0.0)
            if values:
                active_votes.append(float(np.mean(values)))

        if len(active_votes) < sample_count * 0.80:
            return TimerChannelReading(
                "ARC",
                None,
                0.0,
                False,
                "too many arc samples fell outside timer ROI",
                {"sampled": len(active_votes), "requested": sample_count},
            )

        binary_votes = [vote >= 0.5 for vote in active_votes]
        active_fraction = float(np.mean(binary_votes))
        purity = float(np.mean([abs(vote - 0.5) * 2.0 for vote in active_votes]))
        remaining_fraction = active_fraction if self.config.active_represents_remaining else 1.0 - active_fraction
        seconds = max(0.0, min(self.config.max_seconds, remaining_fraction * self.config.max_seconds))
        confidence = max(0.0, min(1.0, purity))
        return TimerChannelReading(
            source="ARC",
            seconds=seconds,
            confidence=confidence,
            valid=True,
            reason="arc color samples measured",
            details={
                "active_fraction": active_fraction,
                "purity": purity,
                "sampled": len(active_votes),
            },
        )


class TimerTracker:
    """Fuse S4 channels conservatively and expose a preparation liveness witness."""

    def __init__(self, config: TimerConfig) -> None:
        self.config = config
        self.digit_reader = DigitTemplateTimerReader(config.digit) if config.digit else None
        self.arc_reader = ArcTimerReader(config.arc) if config.arc else None
        self.previous_seconds: float | None = None
        self.previous_timestamp_ns: int | None = None
        self.valid_streak = 0

    def reset(self) -> None:
        self.previous_seconds = None
        self.previous_timestamp_ns = None
        self.valid_streak = 0

    def _temporal_check(self, seconds: float, timestamp_ns: int) -> tuple[bool, str]:
        if self.previous_seconds is None or self.previous_timestamp_ns is None:
            return True, "timer temporal baseline initialized"
        elapsed = max(0.0, (timestamp_ns - self.previous_timestamp_ns) / 1e9)
        increase = seconds - self.previous_seconds
        decrease = self.previous_seconds - seconds
        # During one preparation phase the countdown should not jump upward.
        if increase > max(1.0, elapsed * 0.75 + 0.5):
            return False, f"timer increased implausibly by {increase:.2f}s"
        if decrease > elapsed * 2.5 + 3.0:
            return False, f"timer decreased implausibly by {decrease:.2f}s over {elapsed:.2f}s"
        return True, "timer temporal movement plausible"

    def observe(self, viewport_bgr: np.ndarray, timestamp_ns: int, stable_phase: GamePhase) -> TimerReading:
        if stable_phase != GamePhase.PREPARATION:
            self.reset()
            return TimerReading(
                seconds=None,
                confidence=0.0,
                source="NONE",
                valid=False,
                valid_streak=0,
                monotonic_ok=True,
                reason="timer only evaluated in stable PREPARATION",
                channels={},
            )

        timer_roi = _crop_normalized(viewport_bgr, self.config.roi)
        readings: list[TimerChannelReading] = []
        if self.digit_reader is not None:
            readings.append(self.digit_reader.read(timer_roi))
        if self.arc_reader is not None:
            readings.append(self.arc_reader.read(timer_roi))

        valid_channels = [item for item in readings if item.valid and item.seconds is not None]
        channels = {item.source: item.to_dict() for item in readings}
        if not valid_channels:
            self.valid_streak = 0
            return TimerReading(None, 0.0, "NONE", False, 0, True, "no valid timer channel", channels)

        if len(valid_channels) >= 2:
            digit = next((item for item in valid_channels if item.source == "DIGIT"), None)
            arc = next((item for item in valid_channels if item.source == "ARC"), None)
            if digit is not None and arc is not None:
                disagreement = abs(float(digit.seconds) - float(arc.seconds))
                if disagreement > self.config.disagreement_seconds:
                    self.valid_streak = 0
                    return TimerReading(
                        seconds=None,
                        confidence=min(digit.confidence, arc.confidence) * 0.5,
                        source="DISAGREE",
                        valid=False,
                        valid_streak=0,
                        monotonic_ok=True,
                        reason=f"digit/arc disagreement {disagreement:.2f}s exceeds tolerance",
                        channels=channels,
                    )
                # Prefer integer OCR value for display; arc is an independent check.
                seconds = float(digit.seconds)
                confidence = min(1.0, 0.55 * digit.confidence + 0.45 * arc.confidence)
                source = "FUSED"
            else:
                best = max(valid_channels, key=lambda item: item.confidence)
                seconds, confidence, source = float(best.seconds), best.confidence, best.source
        else:
            best = valid_channels[0]
            seconds, confidence, source = float(best.seconds), best.confidence, best.source

        temporal_ok, temporal_reason = self._temporal_check(seconds, timestamp_ns)
        valid = confidence >= self.config.min_confidence and temporal_ok
        if valid:
            self.valid_streak += 1
            self.previous_seconds = seconds
            self.previous_timestamp_ns = timestamp_ns
        else:
            self.valid_streak = 0

        return TimerReading(
            seconds=seconds,
            confidence=confidence,
            source=source,
            valid=valid,
            valid_streak=self.valid_streak,
            monotonic_ok=temporal_ok,
            reason=temporal_reason if not valid else f"{source} timer accepted; {temporal_reason}",
            channels=channels,
        )

    def activity_witness(self, reading: TimerReading, stable_phase: GamePhase) -> ActivityWitness:
        if stable_phase != GamePhase.PREPARATION:
            return ActivityWitness(None, "NONE", 0.0, 0.0, "not in stable PREPARATION")
        if not reading.valid or reading.seconds is None:
            return ActivityWitness(None, "NONE", reading.confidence, 0.0, "no valid preparation timer")
        if reading.confidence < self.config.witness_min_confidence:
            return ActivityWitness(None, "NONE", reading.confidence, 0.0, "timer confidence below witness threshold")
        if reading.valid_streak < self.config.witness_min_streak:
            return ActivityWitness(None, "NONE", reading.confidence, 0.0, "timer has not reached witness streak")
        if reading.seconds <= 1.0:
            return ActivityWitness(None, "NONE", reading.confidence, 0.0, "countdown too close to zero for a durable activity expectation")

        hold = min(5.0, max(1.0, float(reading.seconds)))
        return ActivityWitness(
            expected=True,
            source="PREPARATION_TIMER",
            confidence=reading.confidence,
            valid_for_seconds=hold,
            reason="stable preparation countdown should continue changing",
        )
