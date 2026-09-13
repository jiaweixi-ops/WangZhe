from __future__ import annotations

import ctypes
import sys

if sys.platform == "win32":
    from ctypes import wintypes

from .models import MonitorInfo, Rect

if sys.platform == "win32":
    from PySide6 import QtCore, QtGui, QtWidgets


WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

# S1 probe-only signature. The product marker can use normal styling later, but
# the measurement marker must be deliberately easy to identify and hard for
# ordinary game motion to imitate. The probe uses a magenta square border plus
# a cyan crosshair, both fully opaque.
PROBE_MARKER_BORDER_RGB = (255, 0, 255)
PROBE_MARKER_CROSS_RGB = (0, 255, 255)
PROBE_MARKER_INSET = 5
PROBE_MARKER_LINE_WIDTH = 4
PROBE_MARKER_CROSS_HALF = 12


def set_capture_affinity(hwnd: int, affinity: int) -> dict:
    if sys.platform != "win32":
        return {"success": False, "last_error": None, "reason": "not Windows", "affinity": affinity}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ctypes.set_last_error(0)
    ok = bool(user32.SetWindowDisplayAffinity(hwnd, int(affinity)))
    error = ctypes.get_last_error()
    return {
        "success": ok,
        "last_error": int(error),
        "affinity": int(affinity),
    }


def set_capture_exclusion(hwnd: int) -> dict:
    return set_capture_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE)


def clear_capture_exclusion(hwnd: int) -> dict:
    return set_capture_affinity(hwnd, WDA_NONE)


def set_click_through(hwnd: int) -> dict:
    if sys.platform != "win32":
        return {"success": False, "last_error": None, "reason": "not Windows"}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ctypes.set_last_error(0)
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW
    result = user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    error = ctypes.get_last_error()
    return {"success": bool(result or error == 0), "last_error": int(error)}


def _physical_window_rect(hwnd: int) -> Rect:
    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetWindowRect failed")
    return Rect(rect.left, rect.top, rect.right, rect.bottom)


def _move_physical(hwnd: int, x: int, y: int) -> bool:
    return bool(
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            int(x),
            int(y),
            0,
            0,
            SWP_NOSIZE | SWP_NOACTIVATE,
        )
    )


if sys.platform == "win32":
    class SpikeOverlay(QtWidgets.QWidget):
        PANEL_SIZE = (260, 120)
        PROBE_MARKER_SIZE = (72, 72)

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("Qijing Spike Overlay")
            self.setWindowFlags(
                QtCore.Qt.FramelessWindowHint
                | QtCore.Qt.WindowStaysOnTopHint
                | QtCore.Qt.Tool
                | QtCore.Qt.WindowTransparentForInput
            )
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
            self.resize(*self.PANEL_SIZE)
            self._text = "棋镜 Spike\nTEST PLAN"
            self._probe_marker_mode = False
            self.capture_exclusion_result: dict | None = None
            self.click_through_result: dict | None = None
            self.capture_affinity_history: list[dict] = []

        @property
        def hwnd(self) -> int:
            return int(self.winId())

        def physical_rect(self) -> Rect:
            return _physical_window_rect(self.hwnd)

        def set_text(self, text: str) -> None:
            if text == self._text:
                return
            self._text = text
            self.update()

        def set_probe_marker_mode(self, enabled: bool) -> None:
            enabled = bool(enabled)
            if enabled == self._probe_marker_mode:
                return
            self._probe_marker_mode = enabled
            self.resize(*(self.PROBE_MARKER_SIZE if enabled else self.PANEL_SIZE))
            self.update()

        def set_capture_excluded(self, excluded: bool) -> dict:
            result = (
                set_capture_exclusion(self.hwnd)
                if excluded
                else clear_capture_exclusion(self.hwnd)
            )
            result = {**result, "excluded": bool(excluded)}
            self.capture_affinity_history.append(result)
            if excluded:
                self.capture_exclusion_result = result
            return result

        def anchor_outside(
            self,
            game_client: Rect,
            monitor: MonitorInfo | None,
            gap: int = 8,
        ) -> None:
            own = self.physical_rect()
            width, height = own.width, own.height
            work = monitor.work_rect if monitor is not None else game_client
            if game_client.right + gap + width <= work.right:
                x = game_client.right + gap
            else:
                x = max(work.left, game_client.left - gap - width)
            y = min(max(game_client.top, work.top), max(work.top, work.bottom - height))
            _move_physical(self.hwnd, x, y)

        def anchor_inside(self, viewport_screen: Rect, margin: int = 24) -> None:
            own = self.physical_rect()
            x = min(
                viewport_screen.right - own.width - margin,
                viewport_screen.left + margin,
            )
            y = min(
                viewport_screen.bottom - own.height - margin,
                viewport_screen.top + margin,
            )
            x = max(viewport_screen.left, x)
            y = max(viewport_screen.top, y)
            _move_physical(self.hwnd, x, y)

        def showEvent(self, event) -> None:
            super().showEvent(event)
            self.click_through_result = set_click_through(self.hwnd)
            self.set_capture_excluded(True)

        def paintEvent(self, event) -> None:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)

            if self._probe_marker_mode:
                # The S1 probe is an instrument, not a visual-design preview.
                # Use a deterministic opaque two-colour signature so the probe
                # can ask "is this marker present?" instead of inferring it from
                # generic game-pixel differences.
                painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
                border_pen = QtGui.QPen(QtGui.QColor(*PROBE_MARKER_BORDER_RGB, 255))
                border_pen.setWidth(PROBE_MARKER_LINE_WIDTH)
                painter.setPen(border_pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                box = self.rect().adjusted(
                    PROBE_MARKER_INSET,
                    PROBE_MARKER_INSET,
                    -PROBE_MARKER_INSET - 1,
                    -PROBE_MARKER_INSET - 1,
                )
                painter.drawRect(box)

                cross_pen = QtGui.QPen(QtGui.QColor(*PROBE_MARKER_CROSS_RGB, 255))
                cross_pen.setWidth(PROBE_MARKER_LINE_WIDTH)
                painter.setPen(cross_pen)
                cx = self.rect().center().x()
                cy = self.rect().center().y()
                painter.drawLine(cx - PROBE_MARKER_CROSS_HALF, cy, cx + PROBE_MARKER_CROSS_HALF, cy)
                painter.drawLine(cx, cy - PROBE_MARKER_CROSS_HALF, cx, cy + PROBE_MARKER_CROSS_HALF)
                return

            painter.setBrush(QtGui.QColor(22, 26, 34, 225))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
            painter.setPen(QtGui.QColor(240, 244, 250))
            font = painter.font()
            font.setPointSize(12)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                self.rect().adjusted(16, 14, -16, -14),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                self._text,
            )
