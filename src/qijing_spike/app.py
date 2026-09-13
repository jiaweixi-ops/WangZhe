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
from .gates import assess_s0, assess_s1, assess_s2, assess_s3, assess_s4
from .models import FrameFreshness
from .overlay_probe import probe_overlay_exclusion
from .phase import GamePhase, PhaseStateMachine, TemplatePhaseClassifier
from .registration import ReferenceRegistrar
from .stage_profile import load_stage_profile
from .timer import TimerTracker
from .viewport import crop_rect, detect_content_viewport
from .window import WindowTracker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qijing S-1/S0/S1/S2/S3/S4 spike.")
    parser.add_argument("--title", required=True, help="Game window title substring.")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--save-reference", type=Path)
    parser.add_argument("--overlay", action="store_true", help="Show external test overlay.")
    parser.add_argument("--overlay-inside", action="store_true", help="Place test overlay inside the detected game viewport.")
    parser.add_argument("--probe-overlay-exclusion", action="store_true", help="Run positive-control contamination probe inside the game viewport.")
    parser.add_argument("--viewport-aspect", type=float, default=None, help="Optional expected game viewport aspect ratio.")
    parser.add_argument(
        "--stage-profile",
        type=Path,
        default=None,
        help="Versioned S3/S4 JSON profile containing phase templates and optional timer channels.",
    )
    parser.add_argument(
        "--s3-ground-truth",
        choices=["PREPARATION", "COMBAT"],
        default=None,
        help="Controlled-run phase label used only for S3/S4 gate assessment.",
    )
    parser.add_argument("--output-root", type=Path, default=Path("artifacts"))
    return parser


def _run_loop(args, overlay=None, qt_app=None) -> dict:
    tracker = WindowTracker(args.title)
    initial_window = tracker.current
    backend = DxcamBackend()
    health_monitor = FrameHealthMonitor()

    stage_profile = load_stage_profile(args.stage_profile) if args.stage_profile else None
    phase_classifier = TemplatePhaseClassifier(stage_profile.phase) if stage_profile else None
    phase_machine = PhaseStateMachine(stage_profile.phase) if stage_profile else None
    timer_tracker = TimerTracker(stage_profile.timer) if stage_profile and stage_profile.timer else None

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
    phase_rows: list[dict] = []
    timer_rows: list[dict] = []
    liveness_rows: list[dict] = []
    no_new_present_events: list[dict] = []
    window_unavailable_events: list[dict] = []
    capture_errors: list[dict] = []
    capture_gaps: list[dict] = []
    hwnd_events: list[dict] = []

    first_frame_saved = False
    viewport_saved = False
    overlay_probe_result = None
    overlay_probe_done = False
    last_capture_timestamp_ns: int | None = None
    last_new_present_monotonic: float | None = None
    last_anchor_key = None
    s0_paused_seconds = 0.0

    # A timer witness survives short OCR misses/no-present polls only until this
    # deadline. It is never inferred from generic animation.
    witness_until_monotonic = 0.0
    witness_source = "NONE"
    witness_confidence = 0.0

    def clear_stage_state() -> None:
        nonlocal witness_until_monotonic, witness_source, witness_confidence
        health_monitor.reset()
        if phase_machine is not None:
            phase_machine.reset()
        if timer_tracker is not None:
            timer_tracker.reset()
        witness_until_monotonic = 0.0
        witness_source = "NONE"
        witness_confidence = 0.0

    if overlay is not None:
        overlay.set_text("棋镜 Spike\nS-1...S4")
        if args.probe_overlay_exclusion:
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
                clear_stage_state()
                last_new_present_monotonic = None
                hwnd_events.append({"timestamp_ns": time.perf_counter_ns(), "event": "HWND_REACQUIRED", "hwnd": window.hwnd})

            if window.minimized or window.monitor is None:
                witness_until_monotonic = 0.0
                window_unavailable_events.append(
                    {
                        "timestamp_ns": time.perf_counter_ns(),
                        "reason": "window minimized" if window.minimized else "monitor unresolved",
                    }
                )
                continue

            try:
                frame = backend.grab(window.client_rect, window.monitor)
            except Exception as exc:
                capture_errors.append({"timestamp_ns": time.perf_counter_ns(), "error": repr(exc)})
                continue

            if frame is None:
                poll_time = time.monotonic()
                activity_expected = poll_time <= witness_until_monotonic
                seconds_since_present = (
                    None if last_new_present_monotonic is None else max(0.0, poll_time - last_new_present_monotonic)
                )
                stale_no_present = bool(
                    activity_expected
                    and seconds_since_present is not None
                    and seconds_since_present >= health_monitor.stale_after_seconds
                )
                no_new_present_events.append(
                    {
                        "timestamp_ns": time.perf_counter_ns(),
                        "reason": "DXGI/DXcam reported no new desktop present",
                        "activity_expected": activity_expected,
                        "activity_witness_source": witness_source if activity_expected else "NONE",
                        "seconds_since_new_present": seconds_since_present,
                        "stale_suspect": stale_no_present,
                    }
                )
                if activity_expected:
                    liveness_rows.append(
                        {
                            "timestamp_ns": time.perf_counter_ns(),
                            "activity_expected": True,
                            "witness_source": witness_source,
                            "witness_confidence": witness_confidence,
                            "new_present": False,
                            "seconds_since_new_present": seconds_since_present,
                            "stale_suspect": stale_no_present,
                            "reason": "no new present while preparation timer witness is active",
                        }
                    )
                continue

            current_monotonic = time.monotonic()
            last_new_present_monotonic = current_monotonic
            if last_capture_timestamp_ns is not None:
                gap_seconds = (frame.capture_timestamp_ns - last_capture_timestamp_ns) / 1e9
                if gap_seconds > max(0.5, interval * 2.5):
                    capture_gaps.append(
                        {
                            "timestamp_ns": frame.capture_timestamp_ns,
                            "gap_seconds": gap_seconds,
                            "note": "informational new-present gap; S3/S4 witness determines whether it is stale evidence",
                        }
                    )
            last_capture_timestamp_ns = frame.capture_timestamp_ns

            if not first_frame_saved:
                cv2.imwrite(str(out_dir / "first_frame.jpg"), frame.image_bgr)
                first_frame_saved = True

            # Viewport must be established before all S3/S4 ROI work.
            viewport_result = detect_content_viewport(frame.image_bgr, expected_aspect_ratio=args.viewport_aspect)
            viewport_rows.append({"frame_id": frame.frame_id, "timestamp_ns": frame.capture_timestamp_ns, **viewport_result.to_dict()})
            viewport_image = crop_rect(frame.image_bgr, viewport_result.rect)
            if not viewport_saved:
                cv2.imwrite(str(out_dir / "viewport.jpg"), viewport_image)
                viewport_saved = True

            if args.save_reference and not args.save_reference.exists():
                args.save_reference.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(args.save_reference), viewport_image)

            stable_phase = GamePhase.UNKNOWN
            phase_decision = None
            if phase_classifier is not None and phase_machine is not None:
                raw_phase = phase_classifier.observe(viewport_image)
                phase_decision = phase_machine.update(raw_phase)
                stable_phase = phase_decision.stable_phase
                if phase_decision.changed and timer_tracker is not None:
                    timer_tracker.reset()
                phase_rows.append(
                    {
                        "frame_id": frame.frame_id,
                        "timestamp_ns": frame.capture_timestamp_ns,
                        "preparation_support": raw_phase.preparation_support,
                        "combat_support": raw_phase.combat_support,
                        "margin": raw_phase.margin,
                        "raw_reason": raw_phase.reason,
                        "signals": [signal.to_dict() for signal in raw_phase.signals],
                        **phase_decision.to_dict(),
                    }
                )

            timer_reading = None
            timer_witness = None
            if timer_tracker is not None:
                timer_reading = timer_tracker.observe(viewport_image, frame.capture_timestamp_ns, stable_phase)
                timer_witness = timer_tracker.activity_witness(timer_reading, stable_phase)
                if timer_witness.expected is True:
                    witness_until_monotonic = current_monotonic + timer_witness.valid_for_seconds
                    witness_source = timer_witness.source
                    witness_confidence = timer_witness.confidence
                elif stable_phase != GamePhase.PREPARATION:
                    witness_until_monotonic = 0.0
                    witness_source = "NONE"
                    witness_confidence = 0.0

                held_expected = current_monotonic <= witness_until_monotonic
                timer_rows.append(
                    {
                        "frame_id": frame.frame_id,
                        "timestamp_ns": frame.capture_timestamp_ns,
                        "stable_phase": stable_phase.value,
                        **timer_reading.to_dict(),
                        "activity_expected": held_expected,
                        "activity_witness": timer_witness.to_dict(),
                    }
                )

            activity_expected = current_monotonic <= witness_until_monotonic
            health = health_monitor.observe(frame, activity_expected=activity_expected)
            health_rows.append(
                {
                    "frame_id": frame.frame_id,
                    "sequence_id": frame.sequence_id,
                    "timestamp_ns": frame.capture_timestamp_ns,
                    "monitor_index": frame.monitor_index,
                    "clipped": frame.clipped,
                    "reused_cached": frame.reused_cached,
                    "activity_expected": activity_expected,
                    "activity_witness_source": witness_source if activity_expected else "NONE",
                    **health.to_dict(),
                }
            )
            if activity_expected:
                liveness_rows.append(
                    {
                        "frame_id": frame.frame_id,
                        "timestamp_ns": frame.capture_timestamp_ns,
                        "activity_expected": True,
                        "witness_source": witness_source,
                        "witness_confidence": witness_confidence,
                        "new_present": True,
                        "seconds_since_new_present": 0.0,
                        "stale_suspect": health.freshness == FrameFreshness.STALE_SUSPECT,
                        "reason": health.reason,
                    }
                )

            viewport_screen = viewport_result.rect.translated(frame.region.left, frame.region.top)

            if overlay is not None and qt_app is not None:
                if args.probe_overlay_exclusion and not overlay_probe_done:
                    probe_started = time.monotonic()
                    overlay_probe_result = probe_overlay_exclusion(
                        backend=backend,
                        window=window,
                        overlay=overlay,
                        qt_app=qt_app,
                        viewport_screen=viewport_screen,
                        baseline_frame=frame,
                    )
                    s0_paused_seconds += max(0.0, time.monotonic() - probe_started)
                    overlay_probe_done = True
                    last_capture_timestamp_ns = None
                    last_new_present_monotonic = None
                    clear_stage_state()
                    next_tick = time.monotonic() + interval

                anchor_key = (window.client_rect, viewport_screen, args.overlay_inside or args.probe_overlay_exclusion)
                if anchor_key != last_anchor_key:
                    if args.overlay_inside or args.probe_overlay_exclusion:
                        overlay.show()
                        qt_app.processEvents()
                        overlay.anchor_inside(viewport_screen)
                    else:
                        overlay.anchor_outside(window.client_rect, window.monitor)
                    last_anchor_key = anchor_key

                if not (args.probe_overlay_exclusion and not overlay_probe_done):
                    timer_text = ""
                    if timer_reading is not None and timer_reading.valid and timer_reading.seconds is not None:
                        timer_text = f" {timer_reading.seconds:.0f}s"
                    overlay.set_text(f"棋镜 Spike\n{stable_phase.value}{timer_text}")
                qt_app.processEvents()

            if registrar is not None and health.freshness != FrameFreshness.BLACK:
                registration_rows.append(
                    {
                        "frame_id": frame.frame_id,
                        "timestamp_ns": frame.capture_timestamp_ns,
                        "viewport_mode": viewport_result.mode,
                        "viewport_rect": viewport_result.rect.as_region(),
                        **registrar.estimate(reference, viewport_image).to_dict(),
                    }
                )
    finally:
        backend.close()

    s0_observed_seconds = max(0.1, float(args.duration) - s0_paused_seconds)
    s0 = assess_s0(
        health_rows=health_rows,
        no_new_presents=len(no_new_present_events),
        capture_errors=len(capture_errors),
        window_unavailable=len(window_unavailable_events),
        gap_count=len(capture_gaps),
        observed_seconds=s0_observed_seconds,
        liveness_rows=liveness_rows,
    )
    s1 = assess_s1(overlay_probe_result, external_overlay_available=overlay is not None)
    s2 = assess_s2(registration_rows)
    s3 = assess_s3(phase_rows, expected_phase=args.s3_ground_truth)
    s4 = assess_s4(timer_rows, expected_phase=args.s3_ground_truth)

    evidence = {
        "initial_window": initial_window.to_dict(),
        "final_window": tracker.current.to_dict(),
        "hwnd_reacquire_count": tracker.reacquire_count,
        "hwnd_events": hwnd_events,
        "capture_backend": backend.describe(),
        "duration_seconds": args.duration,
        "s0_observed_seconds": s0_observed_seconds,
        "s0_paused_seconds": s0_paused_seconds,
        "target_fps": args.fps,
        "stage_profile": str(stage_profile.source_path) if stage_profile else None,
        "s3_ground_truth": args.s3_ground_truth,
        "captured_new_present_frames": len(health_rows),
        "no_new_present_count": len(no_new_present_events),
        "window_unavailable_count": len(window_unavailable_events),
        "capture_errors": capture_errors,
        "capture_gap_count": len(capture_gaps),
        "phase_samples": len(phase_rows),
        "timer_samples": len(timer_rows),
        "liveness_samples": len(liveness_rows),
        "overlay": {
            "enabled": overlay is not None,
            "inside": bool(args.overlay_inside or args.probe_overlay_exclusion),
            "capture_exclusion": getattr(overlay, "capture_exclusion_result", None),
            "capture_affinity_history": getattr(overlay, "capture_affinity_history", None),
            "click_through": getattr(overlay, "click_through_result", None),
            "probe": overlay_probe_result,
        },
        "gates": {"S0": s0, "S1": s1, "S2": s2, "S3": s3, "S4": s4},
        "environment": collect_environment(),
        "output_dir": str(out_dir),
    }

    files = {
        "health.json": health_rows,
        "viewport.json": viewport_rows,
        "registration.json": registration_rows,
        "phase.json": phase_rows,
        "timer.json": timer_rows,
        "liveness.json": liveness_rows,
        "no_new_presents.json": no_new_present_events,
        "window_unavailable.json": window_unavailable_events,
        "capture_gaps.json": capture_gaps,
        "evidence.json": evidence,
    }
    for name, payload in files.items():
        (out_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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
