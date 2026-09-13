from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys

import cv2
import numpy as np

from .window import enumerate_monitors


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _video_controllers() -> list[dict]:
    if sys.platform != "win32":
        return []
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,DriverVersion,CurrentHorizontalResolution,CurrentVerticalResolution | "
        "ConvertTo-Json -Compress",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        data = json.loads(proc.stdout)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def collect_environment() -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "packages": {
            "dxcam": _version("dxcam"),
            "PySide6": _version("PySide6"),
            "pywin32": _version("pywin32"),
            "psutil": _version("psutil"),
        },
        "monitors": [m.to_dict() for m in enumerate_monitors()],
        "video_controllers": _video_controllers(),
    }
