#!/usr/bin/env python3
"""Проверка плеча на Bybit: текущее значение и ответ на запрос 43x."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.exchange.bybit_adapter import BybitAdapter


async def main() -> None:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    requested = int(sys.argv[2]) if len(sys.argv) > 2 else 43

    cfg = load_config()
    ex = BybitAdapter(cfg)
    try:
        cur = await ex.get_symbol_leverage(symbol)
        test = await ex.apply_trade_leverage(symbol, requested)
        print(f"Символ: {symbol}")
        print(f"Текущее плечо на бирже: {cur}x")
        print(f"Запрос {requested}x -> applied={test.applied}x target={test.target}x ok={test.ok}")
        if test.error:
            print(f"Примечание: {test.error}")
        if test.mismatch:
            print("⚠️ Супервизор мог бы просить больше — биржа ограничила плечо.")
    finally:
        await ex.close()


if __name__ == "__main__":
    asyncio.run(main())
