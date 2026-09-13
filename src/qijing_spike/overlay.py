from __future__ import annotations

import ctypes
import sys

from .models import Rect

if sys.platform == "win32":
    from PySide6 import QtCore, QtGui, QtWidgets


WDA_EXCLUDEFROMCAPTURE = 0x00000011
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080


def set_capture_exclusion(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


def set_click_through(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    try:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        return True
    except Exception:
        return False


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

        def set_text(self, text: str) -> None:
            self._text = text
            self.update()

        def anchor_outside(self, game_client: Rect, gap: int = 8) -> None:
            screen = QtGui.QGuiApplication.screenAt(
                QtCore.QPoint(game_client.left, game_client.top)
            ) or QtGui.QGuiApplication.primaryScreen()
            avail = screen.availableGeometry()
            if game_client.right + gap + self.width() <= avail.right():
                x = game_client.right + gap
            else:
                x = max(avail.left(), game_client.left - gap - self.width())
            y = min(
                max(game_client.top, avail.top()),
                max(avail.top(), avail.bottom() - self.height()),
            )
            self.move(x, y)

        def showEvent(self, event) -> None:
            super().showEvent(event)
            hwnd = int(self.winId())
            set_click_through(hwnd)
            set_capture_exclusion(hwnd)

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
