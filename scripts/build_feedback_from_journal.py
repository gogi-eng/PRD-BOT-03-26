#!/usr/bin/env python3
"""Экспорт честных меток из data/trades/trade_history.jsonl → signal_only_feedback_data.json"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import BotConfig
from prd_agent.evolution.feedback_dataset import (
    filter_quality_rows,
    load_journal_pairs,
    merge_journal_into_dataset,
)


def main() -> int:
    cfg = BotConfig.load(str(ROOT / "config.yaml"))
    journal = ROOT / "data" / "trades" / "trade_history.jsonl"
    ds_rel = str(cfg.get("feedback_loop", "dataset_path", default="signal_only_feedback_data.json"))
    dataset = ROOT / ds_rel if not Path(ds_rel).is_absolute() else Path(ds_rel)

    pairs = load_journal_pairs(journal)
    min_p = float(cfg.get("feedback_loop", "min_feedback_label_abs_pnl_pct", default=0.25) or 0.25)
    min_h = float(cfg.get("feedback_loop", "min_feedback_label_hold_minutes", default=5) or 5)
    soft_p = float(cfg.get("feedback_loop", "soft_min_feedback_label_abs_pnl_pct", default=0.12) or 0.12)
    soft_h = float(cfg.get("feedback_loop", "soft_min_feedback_label_hold_minutes", default=3) or 3)

    quality = filter_quality_rows(
        pairs,
        min_abs_pnl_pct=min_p,
        min_hold_minutes=min_h,
        include_soft=bool(cfg.get("feedback_loop", "include_soft_labels", default=True)),
        soft_min_abs_pnl_pct=soft_p,
        soft_min_hold_minutes=soft_h,
    )
    added = merge_journal_into_dataset(dataset, quality)
    print(
        f"journal pairs={len(pairs)} quality={len(quality)} added={added} -> {dataset}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
