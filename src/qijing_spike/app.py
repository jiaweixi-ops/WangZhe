from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

from .capture import DxcamBackend
from .dpi import enable_per_monitor_v2
from .freshness import FrameHealthMonitor
from .models import FrameFreshness
from .registration import ReferenceRegistrar
from .viewport import crop_rect, detect_content_viewport
from .window import find_best_window


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qijing capture/registration spike.")
    parser.add_argument("--title", required=True, help="Game window title substring.")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--save-reference", type=Path)
    parser.add_argument("--overlay", action="store_true")
    parser.add_argument(
        "--expected-change",
        action="store_true",
        help="Treat repeated content as suspicious during freshness checks.",
    )
    parser.add_argument("--output-root", type=Path, default=Path("artifacts"))
    return parser


def _run_loop(args, overlay=None, qt_app=None) -> dict:
    window = find_best_window(args.title)
    backend = DxcamBackend()
    health_monitor = FrameHealthMonitor()
    registrar = ReferenceRegistrar()
    reference = cv2.imread(str(args.reference)) if args.reference else None
    if args.reference and reference is None:
        raise RuntimeError(f"Could not read reference image: {args.reference}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.output_root / f"spike-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    interval = 1.0 / max(args.fps, 0.5)
    deadline = time.monotonic() + max(args.duration, 0.1)
    next_tick = time.monotonic()

    health_rows = []
    first_frame_saved = False
    viewport_saved = False
    registration_rows = []
    none_grabs = 0

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_tick:
                if qt_app:
                    qt_app.processEvents()
                time.sleep(min(0.01, next_tick - now))
                continue
            next_tick += interval

            # Refresh the window rect every iteration, so window movement is supported.
            window = find_best_window(args.title)
            if overlay is not None:
                overlay.anchor_outside(window.client_rect)
                overlay.set_text("棋镜 Spike\nS0/S1/S2 采集中")
                qt_app.processEvents()

            frame = backend.grab(window.client_rect)
            if frame is None:
                none_grabs += 1
                continue

            health = health_monitor.observe(frame, expected_change=args.expected_change)
            health_rows.append({
                "frame_id": frame.frame_id,
                "sequence_id": frame.sequence_id,
                "timestamp_ns": frame.capture_timestamp_ns,
                **health.to_dict(),
            })

            if not first_frame_saved:
                cv2.imwrite(str(out_dir / "first_frame.jpg"), frame.image_bgr)
                first_frame_saved = True

            viewport = detect_content_viewport(frame.image_bgr)
            viewport_image = crop_rect(frame.image_bgr, viewport)
            if not viewport_saved:
                cv2.imwrite(str(out_dir / "viewport.jpg"), viewport_image)
                viewport_saved = True

            if args.save_reference and not args.save_reference.exists():
                args.save_reference.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(args.save_reference), viewport_image)

            if reference is not None and health.freshness != FrameFreshness.BLACK:
                registration_rows.append(
                    registrar.estimate(reference, viewport_image).to_dict()
                )
    finally:
        backend.close()

    counts: dict[str, int] = {}
    for row in health_rows:
        counts[row["freshness"]] = counts.get(row["freshness"], 0) + 1

    evidence = {
        "window": window.to_dict(),
        "capture_backend": "dxcam-dxgi",
        "duration_seconds": args.duration,
        "target_fps": args.fps,
        "captured_frames": len(health_rows),
        "none_grabs": none_grabs,
        "freshness_counts": counts,
        "latest_registration": registration_rows[-1] if registration_rows else None,
        "output_dir": str(out_dir),
    }
    (out_dir / "health.json").write_text(
        json.dumps(health_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return evidence


def main() -> int:
    args = build_parser().parse_args()
    dpi_api = enable_per_monitor_v2()

    qt_app = None
    overlay = None
    if args.overlay:
        if sys.platform != "win32":
            raise RuntimeError("Overlay is Windows-only.")
        from PySide6 import QtWidgets
        from .overlay import SpikeOverlay

        qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        overlay = SpikeOverlay()
        overlay.show()
        qt_app.processEvents()

    evidence = _run_loop(args, overlay=overlay, qt_app=qt_app)
    evidence["dpi_awareness"] = dpi_api
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
