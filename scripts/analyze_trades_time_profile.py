#!/usr/bin/env python3
"""
Профиль сделок по времени суток и дням недели: PnL, макс. профит, просадка.

Читает data/trades/trade_history.jsonl (+ archive/), время входа в сделку.

Примеры (AGENT-WORLD / PROD):
  cd /root/AGENT-WORLD
  ./venv/bin/python3 scripts/analyze_trades_time_profile.py --hours 168 --tz 3
  cat reports/trades_time_profile_168h.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.analysis.trade_time_profile import (  # noqa: E402
    analyze_trade_time_profile,
    format_trade_time_profile_md,
    report_to_json,
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Analyze max profit/drawdown by hour and weekday (entry time)"
    )
    ap.add_argument("--hours", type=float, default=168.0, help="Окно анализа (168 = 7 суток)")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument(
        "--tz",
        type=float,
        default=3.0,
        help="Смещение от UTC для локального времени (3 = Москва)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Markdown отчёт (по умолчанию reports/trades_time_profile_{hours}h.md)",
    )
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    report = analyze_trade_time_profile(
        args.data_dir,
        hours=args.hours,
        tz_offset_hours=args.tz,
    )
    md = format_trade_time_profile_md(report)

    hours_tag = int(args.hours) if args.hours == int(args.hours) else args.hours
    out_md = args.out or ROOT / "reports" / f"trades_time_profile_{hours_tag}h.md"
    out_json = args.json_out or ROOT / "reports" / f"trades_time_profile_{hours_tag}h.json"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    out_json.write_text(
        json.dumps(report_to_json(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"Time profile ({args.hours:g}h, UTC{args.tz:+.0f}): "
        f"trades={report.trades_total}, "
        f"PnL={report.summary.get('total_pnl', 0):+.2f} -> {out_md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
