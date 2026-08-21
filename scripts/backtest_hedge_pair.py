#!/usr/bin/env python3
"""Synthetic backtest summary for Trend-Continuation Hedge Pair.

Exit 0 if continuation case is profitable and symmetric case is fee-negative.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.strategies.hedge_pair import (
    HedgePairConfig,
    expected_net_on_continuation,
    simulate_pair_path,
)


def _path_continuation(entry: float, cfg: HedgePairConfig) -> List[float]:
    sl = cfg.sl_price_pct / 100.0
    tp = cfg.tp_price_pct / 100.0
    return [entry, entry * (1.0 + sl), entry * (1.0 + tp)]


def _path_reversal(entry: float, cfg: HedgePairConfig) -> List[float]:
    sl = cfg.sl_price_pct / 100.0
    return [entry, entry * (1.0 + sl), entry * (1.0 - sl)]


def _path_symmetric(entry: float, sl_pct: float) -> List[float]:
    return [entry, entry * (1.0 + sl_pct / 100.0)]


def _path_chop(entry: float, cfg: HedgePairConfig) -> List[float]:
    sl = cfg.sl_price_pct / 100.0
    mid = sl * 0.4
    return [
        entry,
        entry * (1.0 + mid),
        entry * (1.0 - mid),
        entry * (1.0 + mid * 0.5),
        entry,
    ]


def main() -> int:
    cfg = HedgePairConfig.from_cfg(
        {
            "hedge_pair": {
                "sl_price_pct": 0.8,
                "tp_to_sl_ratio": 1.8,
                "be_after_profit_pct": 99.0,
                "max_pair_minutes": 120,
            }
        }
    )
    fees_bps = cfg.fee_pct_roundtrip_per_leg * 100.0
    entry = 100.0

    rows: List[Tuple[str, float, str]] = []

    sym_cfg = HedgePairConfig(
        sl_price_pct=1.0,
        tp_price_pct=1.0,
        tp_to_sl_ratio=1.0,
        max_pair_minutes=120,
        be_after_profit_pct=99.0,
    )
    r_sym = simulate_pair_path(_path_symmetric(entry, 1.0), sym_cfg, fees_bps, bias="long")
    rows.append(("symmetric_TP=SL", r_sym.net_pct, r_sym.closed_reason))

    r_cont = simulate_pair_path(_path_continuation(entry, cfg), cfg, fees_bps, bias="long")
    rows.append(("continuation_to_TP", r_cont.net_pct, r_cont.closed_reason))

    r_rev = simulate_pair_path(_path_reversal(entry, cfg), cfg, fees_bps, bias="long")
    rows.append(("reversal_after_SL", r_rev.net_pct, r_rev.closed_reason))

    r_chop = simulate_pair_path(_path_chop(entry, cfg), cfg, fees_bps, bias="long")
    rows.append(("chop_no_hit", r_chop.net_pct, r_chop.closed_reason))

    closed = expected_net_on_continuation(cfg, cfg.fee_pct_roundtrip_per_leg)

    print("Trend-Continuation Hedge Pair — synthetic paths")
    print(f"{'scenario':<22} {'net_%':>10}  reason")
    print("-" * 52)
    for name, net, reason in rows:
        print(f"{name:<22} {net:>10.4f}  {reason}")
    print("-" * 52)
    print(f"closed-form continuation: {closed:.4f}%")

    ok_cont = r_cont.net_pct > 0
    ok_sym = r_sym.net_pct < 0
    if ok_cont and ok_sym:
        print("PASS: continuation profitable, symmetric fee-negative")
        return 0
    print("FAIL: expectancy checks did not hold")
    return 1


if __name__ == "__main__":
    sys.exit(main())
