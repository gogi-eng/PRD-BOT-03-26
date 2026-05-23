#!/usr/bin/env python3
"""
Скан волатильных пар (>1% за 24ч) и теханализ.

Запуск:
  ./venv/bin/python3 scripts/ta_volatility_scan.py
  ./venv/bin/python3 scripts/ta_volatility_scan.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.analysis.volatility_ta import VolatilityTAEngine
from prd_agent.config import load_config
from prd_agent.exchange.bybit_adapter import BybitAdapter


async def main() -> int:
    ap = argparse.ArgumentParser(description="Теханализ волатильных пар Bybit")
    ap.add_argument("--json", action="store_true", help="Вывод JSON")
    ap.add_argument("--min-change", type=float, default=None, help="Мин. |Δ| 24ч, %")
    args = ap.parse_args()

    cfg = load_config(ROOT / "config.yaml")
    engine = VolatilityTAEngine(cfg)
    if args.min_change is not None:
        engine.min_24h_change_pct = args.min_change

    exchange = BybitAdapter(cfg)
    try:
        signals, volatile = await engine.collect_signals(exchange)
    finally:
        await exchange.close()

    if args.json:
        print(
            json.dumps(
                {
                    "volatile": [
                        {
                            "symbol": v.symbol,
                            "change_24h_pct": v.change_24h_pct,
                            "turnover_24h": v.turnover_24h,
                        }
                        for v in volatile
                    ],
                    "signals": [
                        {
                            "symbol": s.symbol,
                            "side": s.side,
                            "confidence": s.confidence,
                            "entry": s.entry,
                            "stop_loss": s.stop_loss,
                            "take_profit": s.take_profit,
                            "reason": s.reason,
                            "indicators": s.indicators,
                        }
                        for s in signals
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"=== Волатильные пары (|Δ24ч| >= {engine.min_24h_change_pct}%) — {len(volatile)} ===\n")
    for v in volatile:
        print(f"  {v.symbol:14}  Δ24h={v.change_24h_pct:5.2f}%  оборот={v.turnover_24h/1e6:.1f}M USDT")

    print(f"\n=== Сигналы теханализа — {len(signals)} ===\n")
    if not signals:
        print("  Нет сигналов (нет совпадения тренда+RSI или RR ниже порога).")
        return 0

    for s in signals:
        ind = s.indicators
        print(
            f"  {s.symbol} {s.side:4}  conf={s.confidence:.0%}  "
            f"вход={s.entry:.6g}  SL={s.stop_loss:.6g}  TP={s.take_profit:.6g}\n"
            f"    RSI={ind.get('rsi')}  RR={ind.get('rr')}  {s.reason}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
