#!/usr/bin/env python3
"""CLI: baseline % SKIP по причинам из signal_ledger (7 дней по умолчанию)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.analysis.signal_ledger import SignalLedger
from prd_agent.telemetry.skip_baseline import format_skip_baseline_text, skip_baseline_report


def main() -> int:
    parser = argparse.ArgumentParser(description="ALGO skip baseline from ledger")
    parser.add_argument("--root", type=Path, default=ROOT, help="Project root")
    parser.add_argument("--hours", type=float, default=168, help="Period hours (default 7d)")
    args = parser.parse_args()

    ledger_dir = args.root / "data" / "ledger"
    ledger = SignalLedger(ledger_dir)
    report = skip_baseline_report(ledger, hours=args.hours)
    print(format_skip_baseline_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
