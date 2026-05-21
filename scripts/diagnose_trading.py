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
from prd_agent.market.symbol_scanner import SymbolScanner
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

    scanner = SymbolScanner(cfg)
    if scanner.enabled():
        syms = await scanner.scan(ex)
        print(f"Скан Bybit (min_24h_volume={scanner.min_24h_volume_usdt:.0f}): {len(syms)} пар")
        print("  ", ", ".join(syms))
    else:
        syms = cfg.get("trading", {}).get("symbols", ["BTCUSDT"])
        print(f"Скан отключён — symbols из config: {', '.join(syms)}")
    tcfg = cfg.get("trading", {})
    min_conf = float(tcfg.get("min_signal_confidence", 0.62))
    min_own = float(tcfg.get("min_own_agent_confidence", getattr(router, "_min_own_conf", 0.28)))

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
            ok = conf >= min_own and abs(score) >= 0.12
            print(
                f"{sym}: score={score:+.3f} conf={conf:.3f} "
                f"(порог агентов {min_own}) -> {'ПРОЙДЁТ' if ok else 'ОТСЕЧЁТ'}"
            )

    sig = cfg.get("signals", {})
    tg_path = (ROOT / sig.get("telegram_signals_jsonl", "reports/telegram_signals/signals.jsonl")).resolve()
    print("\n--- Внешние агенты ---")
    if sig.get("telegram_inbox_enabled", True):
        if tg_path.exists():
            lines = [ln for ln in tg_path.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
            print(f"Telegram inbox: {tg_path}")
            print(f"  строк в файле: {len(lines)}, размер {tg_path.stat().st_size} байт")
            if lines:
                print(f"  последняя: {lines[-1][:120]}...")
        else:
            print(f"Telegram inbox: файл НЕТ — {tg_path}")
            print("  Запустите отдельно: python3 scripts/telegram_signal_agent.py")
    else:
        print("Telegram inbox: отключён в config")
    if router._tg_inbox:
        fresh = router._tg_inbox.poll()
        print(f"  новых строк с прошлого poll: {len(fresh)}")
    if router._whale:
        whale_raw = await router._whale.collect(ex, syms)
        print(f"Whale/News сырых: {len(whale_raw)}")
        for w in whale_raw[:3]:
            print(f"  {w.symbol} {w.side} conf={w.confidence:.3f} src={w.source}")
    else:
        print("Whale/News: отключён")

    own = await router.collect_own_signals(ex, syms)
    tg = router.collect_telegram_signals()
    whale = await router.collect_whale_news(ex, syms)
    print(f"Источники: own={len(own)} telegram={len(tg)} whale={len(whale)}")
    sigs = router.merge_and_rank(own + tg + whale)
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

    min_tg = float(tcfg.get("min_telegram_confidence", min_conf))
    print(f"Пороги: агенты={min_own}, telegram={min_tg}, гибрид/whale={min_conf}")
    pcfg = cfg.get("positions", {})
    print(
        f"Трейлинг позиций: {'ВКЛ' if pcfg.get('trailing_enabled', True) else 'ВЫКЛ'}, "
        f"подхват ручных: {pcfg.get('adopt_manual', True)}"
    )
    pos = await ex.get_positions()
    print(f"Открытых позиций на бирже: {len(pos)}")
    for p in pos:
        print(
            f"  {p.get('symbol')} {p.get('side')} size={p.get('size')} "
            f"SL={p.get('stopLoss', 0)} uPnL={p.get('unrealisedPnl', 0)}"
        )
    print(f"auto_start в config: {tcfg.get('auto_start', False)}")
    await ex.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
