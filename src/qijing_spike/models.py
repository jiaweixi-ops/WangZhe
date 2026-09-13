from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class RuntimeMode(str, Enum):
    NATIVE_PC = "NATIVE_PC"
    ANDROID_EMULATOR = "ANDROID_EMULATOR"
    CLOUD_STREAM = "CLOUD_STREAM"
    UNKNOWN = "UNKNOWN"


class GateResult(str, Enum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    DEGRADED_SHIPPABLE = "DEGRADED_SHIPPABLE"
    FAIL = "FAIL"


class FrameFreshness(str, Enum):
    FRESH = "FRESH"
    QUIET = "QUIET"
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

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_region(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    def translated(self, dx: int, dy: int) -> "Rect":
        return Rect(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)

    def intersect(self, other: "Rect") -> "Rect":
        return Rect(
            max(self.left, other.left),
            max(self.top, other.top),
            min(self.right, other.right),
            min(self.bottom, other.bottom),
        )


@dataclass(frozen=True)
class MonitorInfo:
    index: int
    handle: int
    rect: Rect
    work_rect: Rect
    device_name: str | None = None
    primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    monitor: MonitorInfo | None = None

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
    monitor_index: int | None = None
    clipped: bool = False
    # True only when a backend explicitly returned its previous image because no
    # new desktop-present event was available. Measurement probes must reject it.
    reused_cached: bool = False


@dataclass
class FrameHealth:
    freshness: FrameFreshness
    black_ratio: float
    mean_luma: float
    content_digest: str
    changed_tiles: int
    max_tile_delta: float
    mean_tile_delta: float
    seconds_since_meaningful_change: float | None
    learned_dynamic: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["freshness"] = self.freshness.value
        return data


@dataclass
class ViewportResult:
    rect: Rect
    mode: str
    confidence: float
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegistrationResult:
    mode: str
    detector: str
    matrix_2x3: list[list[float]] | None
    inliers: int
    matches: int
    inlier_ratio: float
    inlier_error_mean: float | None
    all_match_error_mean: float | None
    all_match_error_p90: float | None
    scale_x: float | None
    scale_y: float | None
    confidence: float
    accepted: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
