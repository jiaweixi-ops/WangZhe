from __future__ import annotations

import hashlib
from collections import deque

import cv2
import numpy as np

from .models import CapturedFrame, FrameFreshness, FrameHealth


class FrameHealthMonitor:
    """Detect black/frozen capture without relying on a whole-frame perceptual hash.

    The monitor downsamples the frame, splits it into tiles and measures local
    absolute differences. Small UI changes (timer/resource digits) can therefore
    count as activity even when most of the frame is static. A stale conclusion
    is only made after the stream has demonstrated recent activity, or when a
    future stage-aware caller explicitly supplies ``activity_expected=True``.
    """

    def __init__(
        self,
        *,
        black_luma_threshold: float = 4.0,
        black_ratio_threshold: float = 0.985,
        analysis_width: int = 384,
        analysis_height: int = 216,
        grid_cols: int = 6,
        grid_rows: int = 4,
        changed_pixel_threshold: int = 8,
        tile_q99_threshold: float = 6.0,
        changed_fraction_threshold: float = 0.004,
        learning_window_seconds: float = 5.0,
        min_activity_events: int = 2,
        stale_after_seconds: float = 2.5,
    ) -> None:
        self.black_luma_threshold = black_luma_threshold
        self.black_ratio_threshold = black_ratio_threshold
        self.analysis_width = analysis_width
        self.analysis_height = analysis_height
        self.grid_cols = grid_cols
        self.grid_rows = grid_rows
        self.changed_pixel_threshold = changed_pixel_threshold
        self.tile_q99_threshold = tile_q99_threshold
        self.changed_fraction_threshold = changed_fraction_threshold
        self.learning_window_seconds = learning_window_seconds
        self.min_activity_events = min_activity_events
        self.stale_after_seconds = stale_after_seconds

        self._previous_small: np.ndarray | None = None
        self._last_change_ns: int | None = None
        self._activity_events_ns: deque[int] = deque()

    def reset(self) -> None:
        self._previous_small = None
        self._last_change_ns = None
        self._activity_events_ns.clear()

    def _small_gray(self, image_bgr: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        return cv2.resize(
            gray,
            (self.analysis_width, self.analysis_height),
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def _digest(gray_small: np.ndarray) -> str:
        return hashlib.blake2b(gray_small.tobytes(), digest_size=8).hexdigest()

    def _tile_deltas(self, current: np.ndarray, previous: np.ndarray) -> tuple[list[float], list[float]]:
        diff = cv2.absdiff(current, previous)
        ys = np.linspace(0, diff.shape[0], self.grid_rows + 1, dtype=int)
        xs = np.linspace(0, diff.shape[1], self.grid_cols + 1, dtype=int)
        q99_values: list[float] = []
        changed_fractions: list[float] = []
        for row in range(self.grid_rows):
            for col in range(self.grid_cols):
                tile = diff[ys[row] : ys[row + 1], xs[col] : xs[col + 1]]
                if tile.size == 0:
                    continue
                q99_values.append(float(np.percentile(tile, 99)))
                changed_fractions.append(float((tile >= self.changed_pixel_threshold).mean()))
        return q99_values, changed_fractions

    def observe(
        self,
        frame: CapturedFrame,
        *,
        activity_expected: bool | None = None,
    ) -> FrameHealth:
        gray = cv2.cvtColor(frame.image_bgr, cv2.COLOR_BGR2GRAY)
        mean_luma = float(gray.mean())
        black_ratio = float((gray <= self.black_luma_threshold).mean())
        small = self._small_gray(frame.image_bgr)
        digest = self._digest(small)
        now_ns = frame.capture_timestamp_ns

        if black_ratio >= self.black_ratio_threshold:
            self._previous_small = small
            return FrameHealth(
                freshness=FrameFreshness.BLACK,
                black_ratio=black_ratio,
                mean_luma=mean_luma,
                content_digest=digest,
                changed_tiles=0,
                max_tile_delta=0.0,
                mean_tile_delta=0.0,
                seconds_since_meaningful_change=None,
                learned_dynamic=False,
                reason="frame is almost entirely black",
            )

        if self._previous_small is None or self._previous_small.shape != small.shape:
            self._previous_small = small
            self._last_change_ns = now_ns
            return FrameHealth(
                freshness=FrameFreshness.FRESH,
                black_ratio=black_ratio,
                mean_luma=mean_luma,
                content_digest=digest,
                changed_tiles=0,
                max_tile_delta=0.0,
                mean_tile_delta=0.0,
                seconds_since_meaningful_change=0.0,
                learned_dynamic=False,
                reason="freshness baseline initialized",
            )

        q99_values, changed_fractions = self._tile_deltas(small, self._previous_small)
        self._previous_small = small

        changed_mask = [
            (q >= self.tile_q99_threshold) or (frac >= self.changed_fraction_threshold)
            for q, frac in zip(q99_values, changed_fractions)
        ]
        changed_tiles = sum(changed_mask)
        max_delta = max(q99_values, default=0.0)
        mean_delta = float(np.mean(q99_values)) if q99_values else 0.0
        meaningful_change = changed_tiles > 0

        if meaningful_change:
            self._last_change_ns = now_ns
            self._activity_events_ns.append(now_ns)

        cutoff_ns = now_ns - int(self.learning_window_seconds * 1e9)
        while self._activity_events_ns and self._activity_events_ns[0] < cutoff_ns:
            self._activity_events_ns.popleft()
        learned_dynamic = len(self._activity_events_ns) >= self.min_activity_events

        seconds_since_change = None
        if self._last_change_ns is not None:
            seconds_since_change = max(0.0, (now_ns - self._last_change_ns) / 1e9)

        expected = learned_dynamic if activity_expected is None else activity_expected
        if meaningful_change:
            freshness = FrameFreshness.FRESH
            reason = f"local activity in {changed_tiles} tile(s)"
        elif expected and seconds_since_change is not None and seconds_since_change >= self.stale_after_seconds:
            freshness = FrameFreshness.STALE_SUSPECT
            reason = (
                f"no meaningful local change for {seconds_since_change:.2f}s "
                "after activity was expected"
            )
        else:
            freshness = FrameFreshness.QUIET
            reason = "no meaningful local change; insufficient evidence for stale capture"

        return FrameHealth(
            freshness=freshness,
            black_ratio=black_ratio,
            mean_luma=mean_luma,
            content_digest=digest,
            changed_tiles=changed_tiles,
            max_tile_delta=max_delta,
            mean_tile_delta=mean_delta,
            seconds_since_meaningful_change=seconds_since_change,
            learned_dynamic=learned_dynamic,
            reason=reason,
        )
