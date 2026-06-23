#!/usr/bin/env python3
"""
Hermes: анализ удачных TP (пропущенные + реальные сделки) → правила входа.

Пример:
  ./venv/bin/python3 scripts/hermes_winning_entry_rules.py
  ./venv/bin/python3 scripts/hermes_winning_entry_rules.py --data-dir /root/AGENT-WORLD/data --hours 336
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.learning.winning_entry_rules import (  # noqa: E402
    WinningEntryRulesAnalyzer,
    build_markdown_report,
    build_telegram_report,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Анализ удачных TP → правила входа (Hermes)")
    ap.add_argument(
        "--data-dir",
        default=str(ROOT / "data"),
        help="Папка data (ledger, trades, supervisor)",
    )
    ap.add_argument("--hours", type=float, default=168.0, help="Окно анализа, часов")
    ap.add_argument("--telegram", action="store_true", help="Вывести отчёт для Telegram (HTML)")
    ap.add_argument("--no-save", action="store_true", help="Не писать файлы в data/learning/")
    args = ap.parse_args()

    analyzer = WinningEntryRulesAnalyzer(Path(args.data_dir))
    report = analyzer.analyze(hours=float(args.hours))

    if args.telegram:
        print(build_telegram_report(report))
    else:
        print(build_markdown_report(report))

    if not args.no_save:
        json_p, md_p = analyzer.save(report)
        print(f"\nСохранено:\n  {json_p}\n  {md_p}", file=sys.stderr)

    return 0 if report.tp_winners > 0 or report.sl_losers > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
