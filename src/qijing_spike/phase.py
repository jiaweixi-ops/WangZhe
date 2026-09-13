from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum

import cv2
import numpy as np

from .stage_profile import PhaseConfig, PhaseSignalConfig


class GamePhase(str, Enum):
    UNKNOWN = "UNKNOWN"
    PREPARATION = "PREPARATION"
    COMBAT = "COMBAT"


def _crop_normalized(image: np.ndarray, roi: tuple[float, float, float, float]) -> np.ndarray:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = roi
    left = max(0, min(w - 1, int(round(x0 * w))))
    top = max(0, min(h - 1, int(round(y0 * h))))
    right = max(left + 1, min(w, int(round(x1 * w))))
    bottom = max(top + 1, min(h, int(round(y1 * h))))
    return image[top:bottom, left:right]


@dataclass(frozen=True)
class SignalObservation:
    name: str
    phase: str
    match_score: float
    threshold: float
    expected_present: bool
    supports_phase: bool
    weight: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RawPhaseObservation:
    phase: GamePhase
    confidence: float
    preparation_support: float
    combat_support: float
    margin: float
    signals: tuple[SignalObservation, ...]
    reason: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["phase"] = self.phase.value
        data["signals"] = [signal.to_dict() for signal in self.signals]
        return data


@dataclass(frozen=True)
class PhaseDecision:
    raw_phase: GamePhase
    raw_confidence: float
    stable_phase: GamePhase
    stable_confidence: float
    candidate_phase: GamePhase
    candidate_frames: int
    unknown_frames: int
    changed: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "raw_phase": self.raw_phase.value,
            "raw_confidence": self.raw_confidence,
            "stable_phase": self.stable_phase.value,
            "stable_confidence": self.stable_confidence,
            "candidate_phase": self.candidate_phase.value,
            "candidate_frames": self.candidate_frames,
            "unknown_frames": self.unknown_frames,
            "changed": self.changed,
            "reason": self.reason,
        }


class TemplatePhaseClassifier:
    """Cheap S3 classifier driven by a versioned set of fixed UI templates.

    It intentionally does not infer a phase from generic whole-frame activity.
    Every phase vote must come from an explicit, reviewable profile signal.
    """

    def __init__(self, config: PhaseConfig) -> None:
        self.config = config
        self._templates: dict[str, np.ndarray] = {}
        for signal in config.signals:
            image = cv2.imread(str(signal.template_path), cv2.IMREAD_GRAYSCALE)
            if image is None or image.size == 0:
                raise RuntimeError(f"could not read phase template: {signal.template_path}")
            self._templates[signal.name] = image

    @staticmethod
    def _match(roi_image: np.ndarray, template_gray: np.ndarray) -> float:
        gray = cv2.cvtColor(roi_image, cv2.COLOR_BGR2GRAY)
        if gray.shape[0] < template_gray.shape[0] or gray.shape[1] < template_gray.shape[1]:
            return 0.0
        result = cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
        if result.size == 0:
            return 0.0
        score = float(np.nanmax(result))
        if not np.isfinite(score):
            return 0.0
        return max(-1.0, min(1.0, score))

    def _observe_signal(self, viewport_bgr: np.ndarray, signal: PhaseSignalConfig) -> SignalObservation:
        roi = _crop_normalized(viewport_bgr, signal.roi)
        score = self._match(roi, self._templates[signal.name])
        present = score >= signal.match_threshold
        supports = present if signal.expected_present else not present
        return SignalObservation(
            name=signal.name,
            phase=signal.phase,
            match_score=score,
            threshold=signal.match_threshold,
            expected_present=signal.expected_present,
            supports_phase=supports,
            weight=max(0.0, signal.weight),
        )

    def observe(self, viewport_bgr: np.ndarray) -> RawPhaseObservation:
        observations = tuple(self._observe_signal(viewport_bgr, signal) for signal in self.config.signals)
        totals = {GamePhase.PREPARATION: 0.0, GamePhase.COMBAT: 0.0}
        supports = {GamePhase.PREPARATION: 0.0, GamePhase.COMBAT: 0.0}

        for obs in observations:
            phase = GamePhase(obs.phase)
            totals[phase] += obs.weight
            if obs.supports_phase:
                supports[phase] += obs.weight

        prep = supports[GamePhase.PREPARATION] / totals[GamePhase.PREPARATION] if totals[GamePhase.PREPARATION] else 0.0
        combat = supports[GamePhase.COMBAT] / totals[GamePhase.COMBAT] if totals[GamePhase.COMBAT] else 0.0
        winner = GamePhase.PREPARATION if prep >= combat else GamePhase.COMBAT
        best = max(prep, combat)
        margin = abs(prep - combat)

        if best < self.config.min_support:
            phase = GamePhase.UNKNOWN
            reason = f"best phase support {best:.3f} below {self.config.min_support:.3f}"
        elif margin < self.config.min_margin:
            phase = GamePhase.UNKNOWN
            reason = f"phase support margin {margin:.3f} below {self.config.min_margin:.3f}"
        else:
            phase = winner
            reason = f"{winner.value} support={best:.3f}, margin={margin:.3f}"

        # Confidence is deliberately conservative: support must be high and the
        # competing phase must be separated. This number is not an accuracy KPI.
        confidence = max(0.0, min(1.0, best * (0.5 + 0.5 * margin))) if phase != GamePhase.UNKNOWN else max(0.0, min(0.49, best * margin))
        return RawPhaseObservation(
            phase=phase,
            confidence=confidence,
            preparation_support=prep,
            combat_support=combat,
            margin=margin,
            signals=observations,
            reason=reason,
        )


class PhaseStateMachine:
    """Debounce raw S3 observations before exposing a stable phase."""

    def __init__(self, config: PhaseConfig) -> None:
        self.config = config
        self.stable_phase = GamePhase.UNKNOWN
        self.stable_confidence = 0.0
        self.candidate_phase = GamePhase.UNKNOWN
        self.candidate_frames = 0
        self.unknown_frames = 0

    def reset(self) -> None:
        self.stable_phase = GamePhase.UNKNOWN
        self.stable_confidence = 0.0
        self.candidate_phase = GamePhase.UNKNOWN
        self.candidate_frames = 0
        self.unknown_frames = 0

    def update(self, raw: RawPhaseObservation) -> PhaseDecision:
        previous = self.stable_phase
        changed = False

        if raw.phase == GamePhase.UNKNOWN or raw.confidence < self.config.min_observation_confidence:
            self.unknown_frames += 1
            self.candidate_phase = GamePhase.UNKNOWN
            self.candidate_frames = 0
            if self.unknown_frames >= self.config.unknown_reset_frames:
                self.stable_phase = GamePhase.UNKNOWN
                self.stable_confidence = 0.0
                changed = previous != self.stable_phase
                reason = "stable phase reset after repeated unknown/low-confidence observations"
            else:
                reason = "holding stable phase while raw observation is unknown/low-confidence"
            return PhaseDecision(
                raw_phase=raw.phase,
                raw_confidence=raw.confidence,
                stable_phase=self.stable_phase,
                stable_confidence=self.stable_confidence,
                candidate_phase=self.candidate_phase,
                candidate_frames=self.candidate_frames,
                unknown_frames=self.unknown_frames,
                changed=changed,
                reason=reason,
            )

        self.unknown_frames = 0
        if raw.phase == self.stable_phase and self.stable_phase != GamePhase.UNKNOWN:
            self.candidate_phase = GamePhase.UNKNOWN
            self.candidate_frames = 0
            self.stable_confidence = 0.7 * self.stable_confidence + 0.3 * raw.confidence
            reason = "raw observation agrees with stable phase"
        else:
            if raw.phase == self.candidate_phase:
                self.candidate_frames += 1
            else:
                self.candidate_phase = raw.phase
                self.candidate_frames = 1

            required = (
                self.config.strong_confirm_frames
                if raw.confidence >= self.config.strong_confidence
                else self.config.confirm_frames
            )
            if self.candidate_frames >= required:
                self.stable_phase = raw.phase
                self.stable_confidence = raw.confidence
                self.candidate_phase = GamePhase.UNKNOWN
                self.candidate_frames = 0
                changed = previous != self.stable_phase
                reason = f"phase committed after {required} confirming frame(s)"
            else:
                reason = f"waiting for phase confirmation ({self.candidate_frames}/{required})"

        return PhaseDecision(
            raw_phase=raw.phase,
            raw_confidence=raw.confidence,
            stable_phase=self.stable_phase,
            stable_confidence=self.stable_confidence,
            candidate_phase=self.candidate_phase,
            candidate_frames=self.candidate_frames,
            unknown_frames=self.unknown_frames,
            changed=changed,
            reason=reason,
        )
