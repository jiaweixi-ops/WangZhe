from __future__ import annotations

import os
import sys
from dataclasses import replace

import psutil

from .models import Rect, RuntimeMode, WindowInfo

if sys.platform == "win32":
    import win32gui
    import win32process


EMULATOR_TOKENS = {
    "mumu", "nemu", "ldplayer", "dnplayer", "nox", "bluestacks",
    "androidemulator", "emulator", "gameloop", "txgameassistant",
}

CLOUD_TOKENS = {
    "cloudgame", "cloud gaming", "wegame cloud", "start cloud",
}


def _client_rect_screen(hwnd: int) -> Rect:
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    cr = win32gui.GetClientRect(hwnd)
    right, bottom = win32gui.ClientToScreen(hwnd, (cr[2], cr[3]))
    return Rect(left, top, right, bottom)


def _window_rect(hwnd: int) -> Rect:
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return Rect(l, t, r, b)


def _process_info(pid: int) -> tuple[str | None, list[str]]:
    try:
        proc = psutil.Process(pid)
        exe = proc.exe() or proc.name()
        parts: list[str] = []
        cursor = proc
        for _ in range(4):
            parts.append(f"{cursor.pid}:{cursor.name()}")
            parent = cursor.parent()
            if not parent:
                break
            cursor = parent
        for child in proc.children(recursive=True)[:20]:
            try:
                parts.append(f"{child.pid}:{child.name()}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return exe, parts
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None, []


def classify_runtime(info: WindowInfo) -> WindowInfo:
    haystack = " ".join(
        [info.title, info.class_name, info.exe or "", *info.process_tree]
    ).lower()

    emulator_hits = sorted(t for t in EMULATOR_TOKENS if t in haystack)
    cloud_hits = sorted(t for t in CLOUD_TOKENS if t in haystack)

    if emulator_hits:
        return replace(
            info,
            runtime_mode=RuntimeMode.ANDROID_EMULATOR,
            runtime_confidence=min(0.98, 0.75 + 0.05 * len(emulator_hits)),
            runtime_reasons=[f"emulator token: {x}" for x in emulator_hits],
        )
    if cloud_hits:
        return replace(
            info,
            runtime_mode=RuntimeMode.CLOUD_STREAM,
            runtime_confidence=min(0.95, 0.7 + 0.05 * len(cloud_hits)),
            runtime_reasons=[f"cloud token: {x}" for x in cloud_hits],
        )

    exe_name = os.path.basename(info.exe or "").lower()
    if exe_name and exe_name not in {"explorer.exe", "applicationframehost.exe"}:
        return replace(
            info,
            runtime_mode=RuntimeMode.UNKNOWN,
            runtime_confidence=0.35,
            runtime_reasons=["no emulator/cloud signature; native status requires manual verification"],
        )
    return info


def enumerate_visible_windows(title_contains: str | None = None) -> list[WindowInfo]:
    if sys.platform != "win32":
        raise RuntimeError("Window discovery requires Windows.")

    needle = (title_contains or "").lower()
    results: list[WindowInfo] = []

    def callback(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return
        if needle and needle not in title.lower():
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe, tree = _process_info(pid)
            item = WindowInfo(
                hwnd=hwnd,
                title=title,
                class_name=win32gui.GetClassName(hwnd),
                pid=pid,
                exe=exe,
                window_rect=_window_rect(hwnd),
                client_rect=_client_rect_screen(hwnd),
                visible=True,
                minimized=bool(win32gui.IsIconic(hwnd)),
                process_tree=tree,
            )
            if item.client_rect.width > 32 and item.client_rect.height > 32:
                results.append(classify_runtime(item))
        except Exception:
            return

    win32gui.EnumWindows(callback, None)
    return results


def find_best_window(title_contains: str) -> WindowInfo:
    candidates = enumerate_visible_windows(title_contains)
    if not candidates:
        raise RuntimeError(f"No visible window matched title substring: {title_contains!r}")
    return max(candidates, key=lambda w: w.client_rect.width * w.client_rect.height)
