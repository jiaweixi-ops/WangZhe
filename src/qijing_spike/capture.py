from __future__ import annotations

import itertools
import re
import sys
import time
from abc import ABC, abstractmethod

import numpy as np

from .models import CapturedFrame, MonitorInfo, Rect


class CaptureBackend(ABC):
    name: str

    @abstractmethod
    def grab(self, region: Rect, monitor: MonitorInfo | None = None) -> CapturedFrame | None:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name}

    def close(self) -> None:
        pass


class DxcamBackend(CaptureBackend):
    """DXGI Desktop Duplication capture via DXcam with monitor-aware routing.

    The backend captures the selected output as a full frame and crops in Python.
    This makes the screen/global-vs-output-local region semantics explicit and
    avoids silently passing virtual-desktop coordinates to a monitor-local API.
    """

    name = "dxcam-dxgi"

    _OUTPUT_RE = re.compile(
        r"Device\[(?P<device>\d+)\]\s+Output\[(?P<output>\d+)\]:\s+"
        r"Res:\((?P<w>\d+),\s*(?P<h>\d+)\).*?Primary:(?P<primary>True|False)"
    )

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("DXcam backend is Windows-only.")
        import dxcam

        self._dxcam = dxcam
        self._counter = itertools.count(1)
        self._cameras: dict[tuple[int, int], object] = {}
        self._output_catalog = self._parse_output_info(dxcam.output_info())
        self._last_route: dict | None = None

    @classmethod
    def _parse_output_info(cls, text: str) -> list[dict]:
        outputs: list[dict] = []
        for match in cls._OUTPUT_RE.finditer(text or ""):
            outputs.append(
                {
                    "device_idx": int(match.group("device")),
                    "output_idx": int(match.group("output")),
                    "width": int(match.group("w")),
                    "height": int(match.group("h")),
                    "primary": match.group("primary") == "True",
                }
            )
        return outputs

    def _route_for_monitor(self, monitor: MonitorInfo) -> tuple[int, int]:
        candidates = [
            item
            for item in self._output_catalog
            if item["width"] == monitor.rect.width
            and item["height"] == monitor.rect.height
        ]
        primary_matches = [item for item in candidates if item["primary"] == monitor.primary]
        if primary_matches:
            candidates = primary_matches
        if candidates:
            choice = min(candidates, key=lambda item: abs(item["output_idx"] - monitor.index))
            return choice["device_idx"], choice["output_idx"]
        return 0, monitor.index

    def _camera_for_monitor(self, monitor: MonitorInfo):
        route = self._route_for_monitor(monitor)
        camera = self._cameras.get(route)
        if camera is None:
            camera = self._dxcam.create(
                device_idx=route[0], output_idx=route[1], output_color="BGR"
            )
            self._cameras[route] = camera
        self._last_route = {
            "monitor_index": monitor.index,
            "monitor_device": monitor.device_name,
            "device_idx": route[0],
            "output_idx": route[1],
        }
        return camera

    def grab(self, region: Rect, monitor: MonitorInfo | None = None) -> CapturedFrame | None:
        if monitor is None:
            raise RuntimeError("DXcam monitor-aware capture requires MonitorInfo")

        clipped = region.intersect(monitor.rect)
        if clipped.area <= 0:
            return None

        camera = self._camera_for_monitor(monitor)
        full = camera.grab()
        if full is None:
            return None

        expected_hw = (monitor.rect.height, monitor.rect.width)
        if tuple(full.shape[:2]) != expected_hw:
            raise RuntimeError(
                "DXcam output geometry does not match Win32 monitor geometry: "
                f"frame={full.shape[1]}x{full.shape[0]} monitor="
                f"{monitor.rect.width}x{monitor.rect.height}; route={self._last_route}"
            )

        l = clipped.left - monitor.rect.left
        t = clipped.top - monitor.rect.top
        r = clipped.right - monitor.rect.left
        b = clipped.bottom - monitor.rect.top
        image = np.ascontiguousarray(full[t:b, l:r])
        if image.size == 0:
            return None

        seq = next(self._counter)
        return CapturedFrame(
            frame_id=seq,
            sequence_id=seq,
            capture_timestamp_ns=time.perf_counter_ns(),
            image_bgr=image,
            region=clipped,
            backend=self.name,
            monitor_index=monitor.index,
            clipped=clipped != region,
        )

    def describe(self) -> dict:
        return {
            "name": self.name,
            "outputs": self._output_catalog,
            "last_route": self._last_route,
        }

    def close(self) -> None:
        for camera in self._cameras.values():
            try:
                if hasattr(camera, "release"):
                    camera.release()
                else:
                    camera.stop()
            except Exception:
                pass
        self._cameras.clear()
