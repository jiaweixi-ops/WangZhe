from __future__ import annotations

import os
import sys
from dataclasses import replace

import psutil

from .models import MonitorInfo, Rect, RuntimeMode, WindowInfo

if sys.platform == "win32":
    import win32api
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


def enumerate_monitors() -> list[MonitorInfo]:
    if sys.platform != "win32":
        return []
    monitors: list[MonitorInfo] = []
    for index, item in enumerate(win32api.EnumDisplayMonitors()):
        handle = int(item[0])
        info = win32api.GetMonitorInfo(item[0])
        l, t, r, b = info["Monitor"]
        wl, wt, wr, wb = info["Work"]
        monitors.append(
            MonitorInfo(
                index=index,
                handle=handle,
                rect=Rect(l, t, r, b),
                work_rect=Rect(wl, wt, wr, wb),
                device_name=info.get("Device"),
                primary=bool(info.get("Flags", 0) & 1),
            )
        )
    return monitors


def _intersection_area(a: Rect, b: Rect) -> int:
    return a.intersect(b).area


def monitor_for_rect(rect: Rect) -> MonitorInfo | None:
    monitors = enumerate_monitors()
    if not monitors:
        return None
    best = max(monitors, key=lambda m: _intersection_area(rect, m.rect))
    if _intersection_area(rect, best.rect) == 0:
        cx = (rect.left + rect.right) / 2
        cy = (rect.top + rect.bottom) / 2
        best = min(
            monitors,
            key=lambda m: (
                max(m.rect.left - cx, 0, cx - m.rect.right) ** 2
                + max(m.rect.top - cy, 0, cy - m.rect.bottom) ** 2
            ),
        )
    return best


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


def _build_window_info(hwnd: int, *, include_process: bool = True) -> WindowInfo:
    if sys.platform != "win32":
        raise RuntimeError("Window discovery requires Windows.")
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(f"Window handle is no longer valid: {hwnd}")
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    exe, tree = _process_info(pid) if include_process else (None, [])
    client = _client_rect_screen(hwnd)
    item = WindowInfo(
        hwnd=hwnd,
        title=win32gui.GetWindowText(hwnd).strip(),
        class_name=win32gui.GetClassName(hwnd),
        pid=pid,
        exe=exe,
        window_rect=_window_rect(hwnd),
        client_rect=client,
        visible=bool(win32gui.IsWindowVisible(hwnd)),
        minimized=bool(win32gui.IsIconic(hwnd)),
        process_tree=tree,
        monitor=monitor_for_rect(client),
    )
    return classify_runtime(item) if include_process else item


def refresh_window(existing: WindowInfo) -> WindowInfo:
    """Cheap per-frame refresh that preserves expensive process classification."""
    if sys.platform != "win32":
        raise RuntimeError("Window discovery requires Windows.")
    if not win32gui.IsWindow(existing.hwnd):
        raise RuntimeError("tracked hwnd is invalid")
    client = _client_rect_screen(existing.hwnd)
    return replace(
        existing,
        title=win32gui.GetWindowText(existing.hwnd).strip(),
        window_rect=_window_rect(existing.hwnd),
        client_rect=client,
        visible=bool(win32gui.IsWindowVisible(existing.hwnd)),
        minimized=bool(win32gui.IsIconic(existing.hwnd)),
        monitor=monitor_for_rect(client),
    )


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
            item = _build_window_info(hwnd, include_process=True)
            if item.client_rect.width > 32 and item.client_rect.height > 32:
                results.append(item)
        except Exception:
            return

    win32gui.EnumWindows(callback, None)
    return results


def find_best_window(title_contains: str) -> WindowInfo:
    candidates = enumerate_visible_windows(title_contains)
    if not candidates:
        raise RuntimeError(f"No visible window matched title substring: {title_contains!r}")
    return max(candidates, key=lambda w: w.client_rect.area)


class WindowTracker:
    """Pin a HWND and only re-enumerate if it disappears."""

    def __init__(self, title_contains: str) -> None:
        self.title_contains = title_contains
        self.current = find_best_window(title_contains)
        self.reacquire_count = 0

    def refresh(self) -> tuple[WindowInfo, bool]:
        previous_hwnd = self.current.hwnd
        try:
            refreshed = refresh_window(self.current)
            if refreshed.visible and refreshed.client_rect.area > 0:
                self.current = refreshed
                return self.current, False
        except Exception:
            pass
        self.current = find_best_window(self.title_contains)
        changed = self.current.hwnd != previous_hwnd
        if changed:
            self.reacquire_count += 1
        return self.current, changed
