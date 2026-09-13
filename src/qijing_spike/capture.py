from __future__ import annotations

import itertools
import sys
import time
from abc import ABC, abstractmethod

import numpy as np

from .models import CapturedFrame, Rect


class CaptureBackend(ABC):
    name: str

    @abstractmethod
    def grab(self, region: Rect) -> CapturedFrame | None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class DxcamBackend(CaptureBackend):
    """DXGI Desktop Duplication capture via DXcam."""

    name = "dxcam-dxgi"

    def __init__(self, output_idx: int = 0):
        if sys.platform != "win32":
            raise RuntimeError("DXcam backend is Windows-only.")
        import dxcam

        self._camera = dxcam.create(output_idx=output_idx, output_color="BGR")
        self._counter = itertools.count(1)

    def grab(self, region: Rect) -> CapturedFrame | None:
        image = self._camera.grab(region=region.as_region())
        if image is None:
            return None
        seq = next(self._counter)
        return CapturedFrame(
            frame_id=seq,
            sequence_id=seq,
            capture_timestamp_ns=time.perf_counter_ns(),
            image_bgr=np.ascontiguousarray(image),
            region=region,
            backend=self.name,
        )

    def close(self) -> None:
        try:
            self._camera.stop()
        except Exception:
            pass
