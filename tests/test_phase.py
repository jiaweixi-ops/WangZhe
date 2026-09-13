from pathlib import Path

import cv2
import numpy as np

from qijing_spike.phase import GamePhase, PhaseStateMachine, TemplatePhaseClassifier
from qijing_spike.stage_profile import PhaseConfig, PhaseSignalConfig


def _pattern(kind: str, size: int = 20) -> np.ndarray:
    image = np.zeros((size, size, 3), dtype=np.uint8)
    if kind == "prep":
        cv2.rectangle(image, (3, 3), (size - 4, size - 4), (255, 255, 255), 2)
        cv2.line(image, (4, size // 2), (size - 5, size // 2), (255, 255, 255), 2)
    else:
        cv2.circle(image, (size // 2, size // 2), max(3, size // 3), (255, 255, 255), 2)
        cv2.line(image, (size // 2, 3), (size // 2, size - 4), (255, 255, 255), 2)
    return image


def _classifier(tmp_path: Path):
    prep_path = tmp_path / "prep.png"
    combat_path = tmp_path / "combat.png"
    cv2.imwrite(str(prep_path), _pattern("prep"))
    cv2.imwrite(str(combat_path), _pattern("combat"))
    config = PhaseConfig(
        signals=(
            PhaseSignalConfig("prep", "PREPARATION", (0.0, 0.0, 0.5, 1.0), prep_path, 0.80, 1.0, True),
            PhaseSignalConfig("combat", "COMBAT", (0.5, 0.0, 1.0, 1.0), combat_path, 0.80, 1.0, True),
        ),
        min_support=0.55,
        min_margin=0.20,
        min_observation_confidence=0.60,
        confirm_frames=3,
        strong_confirm_frames=2,
        strong_confidence=0.90,
        unknown_reset_frames=4,
    )
    return TemplatePhaseClassifier(config), PhaseStateMachine(config)


def _viewport(*, prep=False, combat=False):
    image = np.zeros((40, 80, 3), dtype=np.uint8)
    if prep:
        image[10:30, 10:30] = _pattern("prep")
    if combat:
        image[10:30, 50:70] = _pattern("combat")
    return image


def test_phase_classifier_identifies_preparation(tmp_path):
    classifier, _ = _classifier(tmp_path)
    raw = classifier.observe(_viewport(prep=True))
    assert raw.phase == GamePhase.PREPARATION
    assert raw.preparation_support == 1.0
    assert raw.combat_support == 0.0
    assert raw.confidence >= 0.9


def test_phase_classifier_matches_scaled_template(tmp_path):
    classifier, _ = _classifier(tmp_path)
    image = np.zeros((50, 100, 3), dtype=np.uint8)
    scaled = cv2.resize(_pattern("prep"), (25, 25), interpolation=cv2.INTER_LINEAR)
    image[12:37, 12:37] = scaled
    raw = classifier.observe(image)
    assert raw.phase == GamePhase.PREPARATION
    prep_signal = next(signal for signal in raw.signals if signal.name == "prep")
    assert prep_signal.best_scale in {1.125, 1.25}


def test_phase_classifier_returns_unknown_when_both_signals_present(tmp_path):
    classifier, _ = _classifier(tmp_path)
    raw = classifier.observe(_viewport(prep=True, combat=True))
    assert raw.phase == GamePhase.UNKNOWN


def test_phase_state_machine_requires_confirming_frames(tmp_path):
    classifier, machine = _classifier(tmp_path)
    raw = classifier.observe(_viewport(prep=True))
    first = machine.update(raw)
    assert first.stable_phase == GamePhase.UNKNOWN
    second = machine.update(raw)
    assert second.stable_phase == GamePhase.PREPARATION
    assert second.changed is True


def test_phase_state_machine_resets_after_repeated_unknown(tmp_path):
    classifier, machine = _classifier(tmp_path)
    prep = classifier.observe(_viewport(prep=True))
    machine.update(prep)
    machine.update(prep)
    unknown = classifier.observe(_viewport())
    for _ in range(4):
        decision = machine.update(unknown)
    assert decision.stable_phase == GamePhase.UNKNOWN
