from __future__ import annotations

import ctypes
import sys

if sys.platform == "win32":
    from ctypes import wintypes

from .models import MonitorInfo, Rect

if sys.platform == "win32":
    from PySide6 import QtCore, QtGui, QtWidgets


WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010


def set_capture_exclusion(hwnd: int) -> dict:
    if sys.platform != "win32":
        return {"success": False, "last_error": None, "reason": "not Windows"}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ctypes.set_last_error(0)
    ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    error = ctypes.get_last_error()
    return {
        "success": ok,
        "last_error": int(error),
        "affinity": WDA_EXCLUDEFROMCAPTURE,
    }


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
            self.resize(260, 120)
            self._text = "棋镜 Spike\nTEST PLAN"
            self.capture_exclusion_result: dict | None = None
            self.click_through_result: dict | None = None

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

        def anchor_outside(
            self,
            game_client: Rect,
            monitor: MonitorInfo | None,
            gap: int = 8,
        ) -> None:
            # All coordinates here are Win32 physical pixels. Qt logical geometry is
            # intentionally not mixed into placement.
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
            self.capture_exclusion_result = set_capture_exclusion(self.hwnd)

        def paintEvent(self, event) -> None:
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
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
