#!/usr/bin/env python3
"""Флаги non_trading_systemd для bash (key=value по строке)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config  # noqa: E402
from prd_agent.positions.block_hours_loser_close import read_trading_hours_ctl_flags  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    args = ap.parse_args()
    flags = read_trading_hours_ctl_flags(load_config(args.config))
    for key, val in flags.items():
        print(f"{key}={1 if val else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
