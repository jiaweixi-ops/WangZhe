from __future__ import annotations

import ctypes
import sys


def enable_per_monitor_v2() -> str:
    """Enable physical-pixel coordinates as early as possible."""
    if sys.platform != "win32":
        return "non-windows"

    user32 = ctypes.windll.user32
    shcore = getattr(ctypes.windll, "shcore", None)

    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)"
    except Exception:
        pass

    if shcore is not None:
        try:
            if shcore.SetProcessDpiAwareness(2) == 0:
                return "SetProcessDpiAwareness(PER_MONITOR)"
        except Exception:
            pass

    try:
        if user32.SetProcessDPIAware():
            return "SetProcessDPIAware"
    except Exception:
        pass

    return "dpi-awareness-failed"
