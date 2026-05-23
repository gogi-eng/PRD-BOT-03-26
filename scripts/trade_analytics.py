#!/usr/bin/env python3
"""Отчёт по журналу сделок. Запуск: python scripts/trade_analytics.py [--hours 24]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.analysis.trade_analytics import build_report  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument(
        "--journal",
        type=Path,
        default=ROOT / "data" / "trades" / "trade_history.jsonl",
    )
    args = ap.parse_args()
    print(build_report(args.journal, args.hours))


if __name__ == "__main__":
    main()
