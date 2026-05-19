#!/usr/bin/env python3
"""Проверка подключения к Bybit testnet и локальных модулей (без ордеров)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.exchange.bybit_adapter import BybitAdapter
from prd_agent.signals.router import SignalRouter


async def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    if not cfg.get("bybit", {}).get("testnet", False):
        print("WARN: bybit.testnet=false — для первой проверки включите testnet в config.yaml")
    ex = BybitAdapter(cfg)
    ok = True
    try:
        bal = await ex.get_balance()
        print(f"OK balance={bal:.4f} USDT | prd_client={ex.uses_prd_client} | testnet={ex.is_testnet}")
        for sym in cfg.get("trading", {}).get("symbols", ["BTCUSDT"])[:2]:
            px = await ex.get_price(sym)
            kl = await ex.get_klines(sym, limit=5)
            print(f"OK {sym} price={px} klines={len(kl)}")
        router = SignalRouter(cfg, ROOT / "data" / "signals")
        sigs = await router.collect_all(ex, cfg.get("trading", {}).get("symbols", ["BTCUSDT"]))
        print(f"OK signals collected={len(sigs)} (multi-agent={'yes' if router._multi_agent else 'no'})")
    except Exception as exc:
        ok = False
        print(f"FAIL {exc}")
    finally:
        await ex.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
