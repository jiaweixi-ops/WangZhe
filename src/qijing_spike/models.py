from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

import numpy as np


class RuntimeMode(str, Enum):
    NATIVE_PC = "NATIVE_PC"
    ANDROID_EMULATOR = "ANDROID_EMULATOR"
    CLOUD_STREAM = "CLOUD_STREAM"
    UNKNOWN = "UNKNOWN"


class GateResult(str, Enum):
    PASS = "PASS"
    DEGRADED_SHIPPABLE = "DEGRADED_SHIPPABLE"
    FAIL = "FAIL"


class FrameFreshness(str, Enum):
    FRESH = "FRESH"
    DUPLICATE_OK = "DUPLICATE_OK"
    STALE_SUSPECT = "STALE_SUSPECT"
    BLACK = "BLACK"


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    def as_region(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    exe: str | None
    window_rect: Rect
    client_rect: Rect
    visible: bool
    minimized: bool
    process_tree: list[str] = field(default_factory=list)
    runtime_mode: RuntimeMode = RuntimeMode.UNKNOWN
    runtime_confidence: float = 0.0
    runtime_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["runtime_mode"] = self.runtime_mode.value
        return data


@dataclass
class CapturedFrame:
    frame_id: int
    sequence_id: int
    capture_timestamp_ns: int
    image_bgr: np.ndarray
    region: Rect
    backend: str


@dataclass
class FrameHealth:
    freshness: FrameFreshness
    black_ratio: float
    mean_luma: float
    perceptual_hash: str
    repeated_count: int
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["freshness"] = self.freshness.value
        return data


@dataclass
class RegistrationResult:
    mode: str
    matrix_2x3: list[list[float]] | None
    inliers: int
    matches: int
    reprojection_error: float | None
    confidence: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
