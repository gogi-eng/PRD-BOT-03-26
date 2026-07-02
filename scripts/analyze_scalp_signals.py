#!/usr/bin/env python3
"""
Анализ скальп / spike-scalp сигналов и сделок.

Примеры:
  python scripts/analyze_scalp_signals.py --hours 168
  python scripts/analyze_scalp_signals.py --data-dir /root/PRD-BOT-ALL/data --out reports/scalp_signals_analysis.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.analysis.scalp_signals import (  # noqa: E402
    analyze_scalp_signals,
    format_scalp_report_md,
    spike_scalp_config_notes,
)
from prd_agent.config import load_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze spike-scalp signals and trades")
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument(
        "--signal-maps",
        type=Path,
        default=None,
        help="Path to hermes_signal_maps.jsonl (optional)",
    )
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "scalp_signals_analysis.md")
    args = ap.parse_args()

    report = analyze_scalp_signals(
        args.data_dir,
        hours=args.hours,
        signal_maps_path=args.signal_maps,
    )

    cfg_path = args.config
    if cfg_path.is_file():
        try:
            cfg = load_config(cfg_path)
            report.config_notes = spike_scalp_config_notes(cfg)
        except Exception as exc:
            report.config_notes = [f"config read error: {exc}"]

    md = format_scalp_report_md(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")

    print(
        f"Scalp analysis ({args.hours:g}h): "
        f"ledger={report.ledger_signals.total}, "
        f"virtual={report.virtual_signals.total}, "
        f"real_trades={report.real_trades.real_trades}, "
        f"-> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
