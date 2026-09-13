from __future__ import annotations

import cv2
import numpy as np

from .models import CapturedFrame, FrameFreshness, FrameHealth


def _dhash(image_bgr: np.ndarray, width: int = 16, height: int = 16) -> str:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (width + 1, height), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bit)
    return f"{value:0{(width * height + 3)//4}x}"


class FrameHealthMonitor:
    def __init__(
        self,
        black_luma_threshold: float = 4.0,
        black_ratio_threshold: float = 0.985,
        stale_repeat_threshold: int = 4,
    ):
        self.black_luma_threshold = black_luma_threshold
        self.black_ratio_threshold = black_ratio_threshold
        self.stale_repeat_threshold = stale_repeat_threshold
        self._last_hash: str | None = None
        self._repeat_count = 0

    def observe(self, frame: CapturedFrame, *, expected_change: bool) -> FrameHealth:
        gray = cv2.cvtColor(frame.image_bgr, cv2.COLOR_BGR2GRAY)
        mean_luma = float(gray.mean())
        black_ratio = float((gray <= self.black_luma_threshold).mean())
        phash = _dhash(frame.image_bgr)

        if phash == self._last_hash:
            self._repeat_count += 1
        else:
            self._repeat_count = 0
            self._last_hash = phash

        if black_ratio >= self.black_ratio_threshold:
            freshness = FrameFreshness.BLACK
            reason = "frame is almost entirely black"
        elif expected_change and self._repeat_count >= self.stale_repeat_threshold:
            freshness = FrameFreshness.STALE_SUSPECT
            reason = "content hash repeated while change was expected"
        elif self._repeat_count:
            freshness = FrameFreshness.DUPLICATE_OK
            reason = "duplicate content; no stale conclusion without expected-change signal"
        else:
            freshness = FrameFreshness.FRESH
            reason = "new perceptual content"

        return FrameHealth(
            freshness=freshness,
            black_ratio=black_ratio,
            mean_luma=mean_luma,
            perceptual_hash=phash,
            repeated_count=self._repeat_count,
            reason=reason,
        )
