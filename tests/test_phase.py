from pathlib import Path

import cv2
import numpy as np

from qijing_spike.phase import GamePhase, PhaseStateMachine, TemplatePhaseClassifier
from qijing_spike.stage_profile import PhaseConfig, PhaseSignalConfig


def _pattern(kind: str) -> np.ndarray:
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    if kind == "prep":
        cv2.rectangle(image, (3, 3), (16, 16), (255, 255, 255), 2)
        cv2.line(image, (4, 10), (15, 10), (255, 255, 255), 2)
    else:
        cv2.circle(image, (10, 10), 7, (255, 255, 255), 2)
        cv2.line(image, (10, 3), (10, 17), (255, 255, 255), 2)
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
    # high-confidence observations use strong_confirm_frames=2
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
