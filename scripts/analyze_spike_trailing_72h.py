#!/usr/bin/env python3
"""
Анализ spike/scalp (15m импульс) и трейлинга за последние N часов.

Читает trade_history (+ archive), signal_ledger и bot.log — в отличие от
analyze_scalp_signals.py не теряет сделки без source и строки без ts.

Пример (AGENT-WORLD, 3 суток):
  cd /root/AGENT-WORLD
  ./venv/bin/python3 scripts/analyze_spike_trailing_72h.py --hours 72
  cat reports/spike_trailing_72h.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.analysis.spike_trailing_report import (  # noqa: E402
    analyze_spike_trailing,
    format_spike_trailing_md,
    report_to_json,
)
from prd_agent.config import load_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Spike/15m impulse trailing analysis (journal + bot.log)"
    )
    ap.add_argument("--hours", type=float, default=72.0, help="Окно анализа (по умолчанию 72 = 3 суток)")
    ap.add_argument("--root", type=Path, default=ROOT, help="Корень бота (bot.log)")
    ap.add_argument("--data-dir", type=Path, default=None, help="data/ (по умолчанию root/data)")
    ap.add_argument("--config", type=Path, default=None, help="config.yaml")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Markdown отчёт (по умолчанию reports/spike_trailing_{hours}h.md)",
    )
    ap.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="JSON с деталями (по умолчанию reports/spike_trailing_{hours}h.json)",
    )
    args = ap.parse_args()

    root = args.root.resolve()
    data_dir = (args.data_dir or root / "data").resolve()
    cfg_path = args.config or root / "config.yaml"
    cfg = None
    if cfg_path.is_file():
        try:
            cfg = load_config(cfg_path)
        except Exception as exc:
            print(f"config warning: {exc}", file=sys.stderr)

    report = analyze_spike_trailing(root, data_dir=data_dir, hours=args.hours, cfg=cfg)
    md = format_spike_trailing_md(report)

    hours_tag = int(args.hours) if args.hours == int(args.hours) else args.hours
    out_md = args.out or root / "reports" / f"spike_trailing_{hours_tag}h.md"
    out_json = args.json_out or root / "reports" / f"spike_trailing_{hours_tag}h.json"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    out_json.write_text(
        json.dumps(report_to_json(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"Spike trailing ({args.hours:g}h): "
        f"journal_pairs={report.trade_pairs_total}, "
        f"spike_pairs={len(report.spike_pairs)}, "
        f"log_spike_entered={report.log_spike_entered}, "
        f"ledger_spike={report.ledger_spike_total} "
        f"-> {out_md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
