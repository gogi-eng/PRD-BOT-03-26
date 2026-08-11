#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI: бэктест trailing/BE+ на ручных — hold (ваши SL/TP) vs manage (бот двигает).

Примеры:
  python scripts/run_manual_trailing_be_backtest.py --demo
  python scripts/run_manual_trailing_be_backtest.py --root /root/AGENT-WORLD --limit 30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _demo_rows() -> List[Dict[str, Any]]:
    from prd_agent.positions.manual_trailing_be_backtest import (
        ManualTrailBeParams,
        compare_hold_vs_manage,
        summarize_comparisons,
    )

    base = 1_700_000_000_000
    # VELVET-like Sell: быстрый ход вниз, потом откат вверх
    kl = []
    for i in range(15):
        c = 0.80 * (1.0 - 0.003 * i)
        kl.append(
            {
                "startTime": base + i * 60_000,
                "open": c * 1.001,
                "high": c * 1.004,
                "low": c * 0.996,
                "close": c,
            }
        )
    for j in range(10):
        c = kl[-1]["close"] * (1.0 + 0.0025 * (j + 1))
        kl.append(
            {
                "startTime": base + (15 + j) * 60_000,
                "open": c * 0.999,
                "high": c * 1.003,
                "low": c * 0.997,
                "close": c,
            }
        )
    params = ManualTrailBeParams()  # как sandbox defaults
    cmp = compare_hold_vs_manage(
        side="Sell",
        entry=0.80,
        stop_loss=0.84,  # широкий ручной SL
        take_profit=0.72,
        klines=kl,
        params=params,
    )
    cmp["symbol"] = "DEMO_VELVET_LIKE"
    rows = [cmp]
    print("=== DEMO: Sell + широкий SL, потом откат ===")
    print(json.dumps(cmp, ensure_ascii=False, indent=2))
    print("SUMMARY:", json.dumps(summarize_comparisons(rows), ensure_ascii=False))
    return rows


def _load_history(path: Path, limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    # предпочитаем origin=manual / source manual
    manual = [
        r
        for r in rows
        if str(r.get("origin", "")).lower() == "manual"
        or str(r.get("source", "")).lower() in ("manual", "user", "adopt_manual")
        or bool(r.get("manual"))
    ]
    use = manual or rows
    return use[-limit:]


def _run_from_root(root: Path, limit: int) -> None:
    import yaml
    from prd_agent.positions.manual_trailing_be_backtest import (
        ManualTrailBeParams,
        compare_hold_vs_manage,
        summarize_comparisons,
    )

    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8")) or {}
    params = ManualTrailBeParams.from_positions_cfg(cfg.get("positions") or {})
    hist = _load_history(root / "data" / "trades" / "trade_history.jsonl", limit)
    if not hist:
        print(f"Нет сделок в {root}/data/trades/trade_history.jsonl — только demo")
        _demo_rows()
        return

    # Без API klines: строим синтетический путь от entry±% по фактическому pnl/exit если есть
    comparisons: List[Dict[str, Any]] = []
    for row in hist:
        entry = float(row.get("entry") or row.get("entry_price") or 0)
        if entry <= 0:
            continue
        side = str(row.get("side") or "Buy")
        sl = float(row.get("stop_loss") or row.get("sl") or 0)
        tp = float(row.get("take_profit") or row.get("tp") or 0)
        exit_px = float(row.get("exit_price") or row.get("exit") or 0)
        # 30 синтетических свечей от entry к exit (или ±3%)
        target = exit_px if exit_px > 0 else (
            entry * 1.03 if side.lower().startswith("b") else entry * 0.97
        )
        base = 1_700_000_000_000
        kl = []
        steps = 30
        for i in range(steps):
            t = i / (steps - 1)
            c = entry + (target - entry) * t
            # небольшой шум-экстремум в сторону прибыли на середине
            bump = (target - entry) * 0.15 * (1.0 if i == steps // 2 else 0.0)
            mid = c + bump
            hi = max(c, mid, entry) * 1.001
            lo = min(c, mid, entry) * 0.999
            kl.append(
                {
                    "startTime": base + i * 60_000,
                    "open": c,
                    "high": hi,
                    "low": lo,
                    "close": c,
                }
            )
        cmp = compare_hold_vs_manage(
            side=side,
            entry=entry,
            stop_loss=sl,
            take_profit=tp,
            klines=kl,
            params=params,
        )
        cmp["symbol"] = str(row.get("symbol") or "?")
        comparisons.append(cmp)

    summary = summarize_comparisons(comparisons)
    print(f"=== {root} manual-trail/BE backtest (n={summary.get('n')}) ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for c in comparisons[:10]:
        print(
            f"{c.get('symbol')}: hold={c['hold']['outcome']}/{c['hold']['pnl_pct']:+.2f}% "
            f"manage={c['manage']['outcome']}/{c['manage']['pnl_pct']:+.2f}% "
            f"Δ={c['delta_pnl_pct']:+.2f}% sl_upd={c['manage']['sl_updates']}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--root", type=str, default="")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()
    if args.demo or not args.root:
        _demo_rows()
        return
    _run_from_root(Path(args.root), args.limit)


if __name__ == "__main__":
    main()
