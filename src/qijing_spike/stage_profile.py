from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VALID_PHASES = {"PREPARATION", "COMBAT"}
DEFAULT_TEMPLATE_SCALES = (0.75, 0.875, 1.0, 1.125, 1.25)


def _norm_rect(value: list[float] | tuple[float, float, float, float], *, name: str) -> tuple[float, float, float, float]:
    if len(value) != 4:
        raise ValueError(f"{name} must contain 4 normalized coordinates")
    x0, y0, x1, y1 = (float(v) for v in value)
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise ValueError(f"{name} must satisfy 0<=x0<x1<=1 and 0<=y0<y1<=1")
    return x0, y0, x1, y1


@dataclass(frozen=True)
class PhaseSignalConfig:
    name: str
    phase: str
    roi: tuple[float, float, float, float]
    template_path: Path
    match_threshold: float = 0.85
    weight: float = 1.0
    expected_present: bool = True
    scales: tuple[float, ...] = DEFAULT_TEMPLATE_SCALES


@dataclass(frozen=True)
class PhaseConfig:
    signals: tuple[PhaseSignalConfig, ...]
    min_support: float = 0.55
    min_margin: float = 0.20
    min_observation_confidence: float = 0.60
    confirm_frames: int = 3
    strong_confirm_frames: int = 2
    strong_confidence: float = 0.90
    unknown_reset_frames: int = 5


@dataclass(frozen=True)
class DigitTimerConfig:
    templates_dir: Path
    min_digit_score: float = 0.52
    max_digits: int = 3


@dataclass(frozen=True)
class ArcTimerConfig:
    center: tuple[float, float]
    radius: float
    thickness: float
    start_angle_deg: float
    sweep_angle_deg: float
    clockwise: bool
    hsv_min: tuple[int, int, int]
    hsv_max: tuple[int, int, int]
    max_seconds: float
    active_represents_remaining: bool = True
    angular_samples: int = 180


@dataclass(frozen=True)
class TimerConfig:
    roi: tuple[float, float, float, float]
    min_confidence: float = 0.70
    disagreement_seconds: float = 1.5
    witness_min_confidence: float = 0.82
    witness_min_streak: int = 2
    digit: DigitTimerConfig | None = None
    arc: ArcTimerConfig | None = None


@dataclass(frozen=True)
class StageProfile:
    source_path: Path
    phase: PhaseConfig
    timer: TimerConfig | None


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (base / path).resolve()


def _load_phase(base: Path, payload: dict[str, Any]) -> PhaseConfig:
    signals: list[PhaseSignalConfig] = []
    for index, raw in enumerate(payload.get("signals", [])):
        phase = str(raw.get("phase", "")).upper()
        if phase not in VALID_PHASES:
            raise ValueError(f"phase.signals[{index}].phase must be PREPARATION or COMBAT")
        template_path = _resolve(base, str(raw["template"]))
        scales = tuple(float(v) for v in raw.get("scales", DEFAULT_TEMPLATE_SCALES))
        if not scales or any(v <= 0 for v in scales):
            raise ValueError(f"phase.signals[{index}].scales must contain positive values")
        signals.append(
            PhaseSignalConfig(
                name=str(raw.get("name", f"signal_{index}")),
                phase=phase,
                roi=_norm_rect(raw["roi"], name=f"phase.signals[{index}].roi"),
                template_path=template_path,
                match_threshold=float(raw.get("match_threshold", 0.85)),
                weight=float(raw.get("weight", 1.0)),
                expected_present=bool(raw.get("expected_present", True)),
                scales=scales,
            )
        )
    if not signals:
        raise ValueError("phase.signals must contain at least one template signal")
    phases_present = {signal.phase for signal in signals}
    missing = VALID_PHASES - phases_present
    if missing:
        raise ValueError(f"phase.signals must include explicit signals for both phases; missing {sorted(missing)}")
    return PhaseConfig(
        signals=tuple(signals),
        min_support=float(payload.get("min_support", 0.55)),
        min_margin=float(payload.get("min_margin", 0.20)),
        min_observation_confidence=float(payload.get("min_observation_confidence", 0.60)),
        confirm_frames=int(payload.get("confirm_frames", 3)),
        strong_confirm_frames=int(payload.get("strong_confirm_frames", 2)),
        strong_confidence=float(payload.get("strong_confidence", 0.90)),
        unknown_reset_frames=int(payload.get("unknown_reset_frames", 5)),
    )


def _load_timer(base: Path, payload: dict[str, Any] | None) -> TimerConfig | None:
    if not payload:
        return None

    digit = None
    raw_digit = payload.get("digit")
    if raw_digit:
        digit = DigitTimerConfig(
            templates_dir=_resolve(base, str(raw_digit["templates_dir"])),
            min_digit_score=float(raw_digit.get("min_digit_score", 0.52)),
            max_digits=int(raw_digit.get("max_digits", 3)),
        )

    arc = None
    raw_arc = payload.get("arc")
    if raw_arc:
        center = tuple(float(v) for v in raw_arc.get("center", [0.5, 0.5]))
        if len(center) != 2 or not all(0.0 <= v <= 1.0 for v in center):
            raise ValueError("timer.arc.center must contain two normalized values")
        hsv_min = tuple(int(v) for v in raw_arc["hsv_min"])
        hsv_max = tuple(int(v) for v in raw_arc["hsv_max"])
        if len(hsv_min) != 3 or len(hsv_max) != 3:
            raise ValueError("timer.arc hsv_min/hsv_max must each contain three values")
        arc = ArcTimerConfig(
            center=(center[0], center[1]),
            radius=float(raw_arc.get("radius", 0.40)),
            thickness=float(raw_arc.get("thickness", 0.08)),
            start_angle_deg=float(raw_arc.get("start_angle_deg", -90.0)),
            sweep_angle_deg=float(raw_arc.get("sweep_angle_deg", 360.0)),
            clockwise=bool(raw_arc.get("clockwise", True)),
            hsv_min=(hsv_min[0], hsv_min[1], hsv_min[2]),
            hsv_max=(hsv_max[0], hsv_max[1], hsv_max[2]),
            max_seconds=float(raw_arc["max_seconds"]),
            active_represents_remaining=bool(raw_arc.get("active_represents_remaining", True)),
            angular_samples=int(raw_arc.get("angular_samples", 180)),
        )

    if digit is None and arc is None:
        raise ValueError("timer must configure at least digit or arc channel")

    return TimerConfig(
        roi=_norm_rect(payload["roi"], name="timer.roi"),
        min_confidence=float(payload.get("min_confidence", 0.70)),
        disagreement_seconds=float(payload.get("disagreement_seconds", 1.5)),
        witness_min_confidence=float(payload.get("witness_min_confidence", 0.82)),
        witness_min_streak=int(payload.get("witness_min_streak", 2)),
        digit=digit,
        arc=arc,
    )


def load_stage_profile(path: Path) -> StageProfile:
    source = path.resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if int(payload.get("version", 1)) != 1:
        raise ValueError("unsupported stage profile version")
    base = source.parent
    return StageProfile(
        source_path=source,
        phase=_load_phase(base, payload["phase"]),
        timer=_load_timer(base, payload.get("timer")),
    )
