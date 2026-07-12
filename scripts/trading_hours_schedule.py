#!/usr/bin/env python3
"""Печать cron-строк stop/start из config.yaml."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config  # noqa: E402
from prd_agent.analysis.trading_hours_schedule import (  # noqa: E402
    format_windows_md,
    ny_open_block_hours_msk,
    read_ny_open_block_settings,
    read_trading_windows,
)
from prd_agent.time_hours import read_timezone_offset  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Trading hours stop/start schedule")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--env", choices=("prod", "world"), default="prod")
    ap.add_argument("--repo-dir", type=Path, default=None)
    ap.add_argument("--print-cron", action="store_true")
    ap.add_argument("--print-md", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    windows = read_trading_windows(cfg)
    tz = read_timezone_offset(cfg)
    repo = (args.repo_dir or ROOT).resolve()
    ctl = repo / "scripts" / "trading_hours_ctl.sh"

    if args.print_md or not args.print_cron:
        print(f"# Неторговые окна ({args.env})")
        for line in format_windows_md(windows, tz_offset=tz):
            print(line)
        ny_raw = read_ny_open_block_settings(cfg)
        if ny_raw:
            ny_hours = sorted(ny_open_block_hours_msk(cfg))
            print("")
            print(
                f"NY open block (DST {ny_raw.get('market_tz', 'America/New_York')}): "
                f"МСК часы {ny_hours}"
            )

    if args.print_cron:
        for w in windows:
            print(
                f"{w.stop_cron} * * * {ctl} stop {args.env}  "
                f"# block {w.start_hour:02d}-{w.end_hour:02d} MSK (нужен CRON_TZ=Europe/Moscow)"
            )
            print(
                f"{w.resume_cron} * * * {ctl} start {args.env}  "
                f"# resume before {w.end_hour + 1:02d}:00 MSK"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
