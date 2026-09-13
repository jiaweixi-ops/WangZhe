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
from .evidence import collect_environment
from .freshness import FrameHealthMonitor
from .gates import assess_s0, assess_s1, assess_s2
from .models import FrameFreshness
from .overlay_probe import probe_overlay_exclusion
from .registration import ReferenceRegistrar
from .viewport import crop_rect, detect_content_viewport
from .window import WindowTracker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qijing S-1/S0/S1/S2 capture spike.")
    parser.add_argument("--title", required=True, help="Game window title substring.")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--save-reference", type=Path)
    parser.add_argument("--overlay", action="store_true", help="Show external test overlay.")
    parser.add_argument(
        "--overlay-inside",
        action="store_true",
        help="Place test overlay inside the detected game viewport.",
    )
    parser.add_argument(
        "--probe-overlay-exclusion",
        action="store_true",
        help="Run positive-control contamination probe inside the game viewport.",
    )
    parser.add_argument(
        "--viewport-aspect",
        type=float,
        default=None,
        help="Optional expected game viewport aspect ratio (for example 1.7777778).",
    )
    parser.add_argument("--output-root", type=Path, default=Path("artifacts"))
    return parser


def _run_loop(args, overlay=None, qt_app=None) -> dict:
    tracker = WindowTracker(args.title)
    initial_window = tracker.current
    backend = DxcamBackend()
    health_monitor = FrameHealthMonitor()

    reference = cv2.imread(str(args.reference)) if args.reference else None
    if args.reference and reference is None:
        raise RuntimeError(f"Could not read reference image: {args.reference}")
    registrar = ReferenceRegistrar() if reference is not None else None
    if registrar is not None:
        registrar.set_reference(reference)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.output_root / f"spike-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    interval = 1.0 / max(args.fps, 0.5)
    deadline = time.monotonic() + max(args.duration, 0.1)
    next_tick = time.monotonic()

    health_rows: list[dict] = []
    viewport_rows: list[dict] = []
    registration_rows: list[dict] = []
    no_new_present_events: list[dict] = []
    capture_errors: list[dict] = []
    capture_gaps: list[dict] = []
    hwnd_events: list[dict] = []
    first_frame_saved = False
    viewport_saved = False
    overlay_probe_result = None
    overlay_probe_done = False
    last_capture_timestamp_ns: int | None = None
    last_anchor_key = None

    if overlay is not None:
        overlay.set_text("棋镜 Spike\nS-1/S0/S1/S2")
        if args.probe_overlay_exclusion:
            # The first normal capture becomes a known-clean baseline frame.
            overlay.hide()
            qt_app.processEvents()

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_tick:
                if qt_app:
                    qt_app.processEvents()
                time.sleep(min(0.02, next_tick - now))
                continue
            next_tick = max(next_tick + interval, now + interval)

            window, hwnd_changed = tracker.refresh()
            if hwnd_changed:
                health_monitor.reset()
                hwnd_events.append(
                    {
                        "timestamp_ns": time.perf_counter_ns(),
                        "event": "HWND_REACQUIRED",
                        "hwnd": window.hwnd,
                    }
                )

            if window.minimized or window.monitor is None:
                capture_errors.append(
                    {
                        "timestamp_ns": time.perf_counter_ns(),
                        "error": "window minimized or monitor unresolved",
                    }
                )
                continue

            try:
                frame = backend.grab(window.client_rect, window.monitor)
            except Exception as exc:
                capture_errors.append(
                    {"timestamp_ns": time.perf_counter_ns(), "error": repr(exc)}
                )
                continue
            if frame is None:
                # DXcam's one-shot semantics: None means no newly presented desktop
                # frame, not a capture failure.
                no_new_present_events.append(
                    {
                        "timestamp_ns": time.perf_counter_ns(),
                        "reason": "DXGI/DXcam reported no new desktop present",
                    }
                )
                continue

            if last_capture_timestamp_ns is not None:
                gap_seconds = (frame.capture_timestamp_ns - last_capture_timestamp_ns) / 1e9
                if gap_seconds > max(0.5, interval * 2.5):
                    capture_gaps.append(
                        {
                            "timestamp_ns": frame.capture_timestamp_ns,
                            "gap_seconds": gap_seconds,
                            "note": "informational new-present gap; not a failure without activity expectation",
                        }
                    )
            last_capture_timestamp_ns = frame.capture_timestamp_ns

            health = health_monitor.observe(frame)
            health_rows.append(
                {
                    "frame_id": frame.frame_id,
                    "sequence_id": frame.sequence_id,
                    "timestamp_ns": frame.capture_timestamp_ns,
                    "monitor_index": frame.monitor_index,
                    "clipped": frame.clipped,
                    "reused_cached": frame.reused_cached,
                    **health.to_dict(),
                }
            )

            if not first_frame_saved:
                cv2.imwrite(str(out_dir / "first_frame.jpg"), frame.image_bgr)
                first_frame_saved = True

            viewport_result = detect_content_viewport(
                frame.image_bgr,
                expected_aspect_ratio=args.viewport_aspect,
            )
            viewport_rows.append(
                {
                    "frame_id": frame.frame_id,
                    "timestamp_ns": frame.capture_timestamp_ns,
                    **viewport_result.to_dict(),
                }
            )
            viewport_image = crop_rect(frame.image_bgr, viewport_result.rect)
            if not viewport_saved:
                cv2.imwrite(str(out_dir / "viewport.jpg"), viewport_image)
                viewport_saved = True

            if args.save_reference and not args.save_reference.exists():
                args.save_reference.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(args.save_reference), viewport_image)

            viewport_screen = viewport_result.rect.translated(frame.region.left, frame.region.top)

            if overlay is not None and qt_app is not None:
                if args.probe_overlay_exclusion and not overlay_probe_done:
                    overlay_probe_result = probe_overlay_exclusion(
                        backend=backend,
                        window=window,
                        overlay=overlay,
                        qt_app=qt_app,
                        viewport_screen=viewport_screen,
                        baseline_frame=frame,
                    )
                    overlay_probe_done = True
                    # Probe sleeps/toggles the compositor by design. Do not charge
                    # its wall time to S0 gap metrics or carry its visual state into
                    # the main freshness baseline.
                    last_capture_timestamp_ns = None
                    health_monitor.reset()
                    next_tick = time.monotonic() + interval

                anchor_key = (
                    window.client_rect,
                    viewport_screen,
                    args.overlay_inside or args.probe_overlay_exclusion,
                )
                if anchor_key != last_anchor_key:
                    if args.overlay_inside or args.probe_overlay_exclusion:
                        overlay.show()
                        qt_app.processEvents()
                        overlay.anchor_inside(viewport_screen)
                    else:
                        overlay.anchor_outside(window.client_rect, window.monitor)
                    last_anchor_key = anchor_key
                qt_app.processEvents()

            if registrar is not None and health.freshness != FrameFreshness.BLACK:
                registration_rows.append(
                    {
                        "frame_id": frame.frame_id,
                        "timestamp_ns": frame.capture_timestamp_ns,
                        **registrar.estimate(reference, viewport_image).to_dict(),
                    }
                )
    finally:
        backend.close()

    s0 = assess_s0(
        health_rows=health_rows,
        no_new_presents=len(no_new_present_events),
        capture_errors=len(capture_errors),
        gap_count=len(capture_gaps),
    )
    s1 = assess_s1(
        overlay_probe_result,
        external_overlay_available=overlay is not None,
    )
    s2 = assess_s2(registration_rows)

    evidence = {
        "initial_window": initial_window.to_dict(),
        "final_window": tracker.current.to_dict(),
        "hwnd_reacquire_count": tracker.reacquire_count,
        "hwnd_events": hwnd_events,
        "capture_backend": backend.describe(),
        "duration_seconds": args.duration,
        "target_fps": args.fps,
        "captured_new_present_frames": len(health_rows),
        "no_new_present_count": len(no_new_present_events),
        "capture_errors": capture_errors,
        "capture_gap_count": len(capture_gaps),
        "overlay": {
            "enabled": overlay is not None,
            "inside": bool(args.overlay_inside or args.probe_overlay_exclusion),
            "capture_exclusion": getattr(overlay, "capture_exclusion_result", None),
            "capture_affinity_history": getattr(overlay, "capture_affinity_history", None),
            "click_through": getattr(overlay, "click_through_result", None),
            "probe": overlay_probe_result,
        },
        "gates": {"S0": s0, "S1": s1, "S2": s2},
        "environment": collect_environment(),
        "output_dir": str(out_dir),
    }

    files = {
        "health.json": health_rows,
        "viewport.json": viewport_rows,
        "registration.json": registration_rows,
        "no_new_presents.json": no_new_present_events,
        "capture_gaps.json": capture_gaps,
        "evidence.json": evidence,
    }
    for name, payload in files.items():
        (out_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return evidence


def main() -> int:
    args = build_parser().parse_args()
    dpi_api = enable_per_monitor_v2()

    qt_app = None
    overlay = None
    wants_overlay = args.overlay or args.overlay_inside or args.probe_overlay_exclusion
    if wants_overlay:
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
    Path(evidence["output_dir"], "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
