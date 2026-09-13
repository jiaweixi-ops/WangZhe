from __future__ import annotations

import argparse
import json

from .dpi import enable_per_monitor_v2
from .window import enumerate_visible_windows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect game/runtime window topology.")
    parser.add_argument("--title", default=None, help="Optional title substring.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    dpi_api = enable_per_monitor_v2()
    windows = enumerate_visible_windows(args.title)
    payload = {
        "dpi_awareness": dpi_api,
        "count": len(windows),
        "windows": [w.to_dict() for w in windows],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if windows else 2


if __name__ == "__main__":
    raise SystemExit(main())
