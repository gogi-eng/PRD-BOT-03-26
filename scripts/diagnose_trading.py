#!/usr/bin/env python3
"""Почему бот не открывает сделки — диагностика без отправки ордеров."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config
from prd_agent.engine.orchestrator import UnifiedOrchestrator
from prd_agent.exchange.bybit_adapter import BybitAdapter
from prd_agent.exchange.order_prep import prepare_market_order
from prd_agent.signals.router import SignalRouter


async def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    ex = BybitAdapter(cfg)
    router = SignalRouter(cfg, ROOT / "data" / "signals")
    orch = UnifiedOrchestrator(cfg)

    print("=== Диагностика торговли (без ордеров) ===")
    print(f"Режим: {'TESTNET' if ex.is_testnet else 'LIVE'}")
    print(f"Bybit client: {'OK' if ex.uses_prd_client else 'НЕТ'}")
    bal = await ex.get_balance()
    print(f"Баланс: {bal:.4f} USDT")
    if bal <= 0:
        print("ПРОБЛЕМА: баланс 0 — сделки не откроются (qty=0).")

    syms = cfg.get("trading", {}).get("symbols", ["BTCUSDT"])
    min_conf = float(cfg.get("trading", {}).get("min_signal_confidence", 0.62))

    if router._multi_agent:
        import pandas as pd

        for sym in syms[:3]:
            kl = await ex.get_klines(sym, limit=120)
            df = pd.DataFrame(kl)
            if df.empty:
                print(f"{sym}: нет свечей")
                continue
            outs = router._multi_agent.get_signals(df)
            score = router._multi_agent.aggregate(outs)
            conf = router._own_agent_confidence(score, outs)
            print(
                f"{sym}: score={score:+.3f} conf={conf:.3f} "
                f"(порог {min_conf}) -> {'ПРОЙДЁТ' if conf >= min_conf and abs(score) >= 0.12 else 'ОТСЕЧЁТ'}"
            )

    sigs = await router.collect_all(ex, syms)
    print(f"Сигналов после фильтра: {len(sigs)}")
    for s in sigs[:5]:
        print(f"  {s.symbol} {s.side} conf={s.confidence:.3f} src={s.source}")

    ok_risk, risk_reason = orch.risk.can_trade(syms[0] if syms else "BTCUSDT")
    print(f"Риск-стоп: {'OK' if ok_risk else 'БЛОК: ' + risk_reason}")

    if sigs and bal > 0:
        s = sigs[0]
        entry = s.entry or await ex.get_price(s.symbol)
        sl = s.stop_loss or (entry * 0.995 if s.side == "Buy" else entry * 1.005)
        tp = s.take_profit or (entry * 1.01 if s.side == "Buy" else entry * 0.99)
        qty = orch.risk.calculate_position_size(bal, orch.risk_pct, entry, sl, orch.leverage)
        qty2, _, _, err = await prepare_market_order(
            ex._client, symbol=s.symbol, leverage=orch.leverage, qty=qty, stop_loss=sl, take_profit=tp
        )
        print(f"Пример ордера {s.symbol} {s.side}: qty_raw={qty:.6f} qty_ready={qty2} err={err or 'OK'}")

    print(f"auto_start в config: {cfg.get('trading', {}).get('auto_start', False)}")
    await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
