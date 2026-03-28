# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI.

## Current Status (обновлено: 2026-03-28)

### Signal Quality Fix (итерация 44, 32/32 passed):
**Проблема**: 80% сигналов разворачивались после входа. Бот входил в конце движения.

**Исправления:**
1. **Orderflow Analyzer v2**: depth 10→25, absorption detection (последние 30 трейдов), weighted imbalance (60% recent + 40% total), новые веса imbalance_score (40% orderbook + 35% trade + 25% weighted)
2. **Exhaustion Guard**: отклоняет если 5+ из 7 последних свечей двигались в направлении сигнала (исчерпание движения)
3. **Counter-Flow Guard**: отклоняет SELL если buy_vol > sell_vol * 1.4 (absorption), аналогично для BUY
4. **Trailing activation**: 1.1→0.7 ATR (trailing активируется раньше, больше trailing_exit)
5. **Trailing distance**: 1.4→1.2 ATR (стоп ближе к цене, лучше lock profit)
6. **Exchange_closed**: confirm 5→8, force 20→30 (реже ложные срабатывания)
7. **HTF ATR floor**: SL рассчитывается по max(1m ATR, 15m ATR)
8. **min_stop_distance**: 0.8%→1.5%

### P2 Features (итерация 43, 69/69 passed):
- RL Position Manager v2 (regime-aware, TIGHTEN action)
- A/B/C Signal Grading
- Multi-TF Support (1m/5m/15m presets)

### P1 Features (итерация 42, 43/43 passed):
- Whitelist-only, Partial TP 30/70, /retrain_status

### P0 Manual Protection (итерация 41, 34/34 passed):
- Manual позиции защищены от force_closed_stale, HARD_SL, LIQUIDATION_STOP, EARLY_EXIT

## Testing: 178/178 total passed
- Iteration 41: 34/34 (P0)
- Iteration 42: 43/43 (P1)
- Iteration 43: 69/69 (P2)
- Iteration 44: 32/32 (Signal Quality)

## Config Changes Summary
```yaml
# Signal quality
exit.trailing_activation_atr: 1.1 → 0.7
exit.trailing_distance_atr: 1.4 → 1.2
market.exchange_closed_confirm_cycles: 5 → 8
market.exchange_closed_force_cycles: 20 → 30
entry.min_stop_distance_pct: 0.8 → 1.5
```

## Critical Rules
- MANUAL TRADES ARE SACRED
- FEE AWARENESS (0.06% taker fees)
- EXHAUSTION GUARD (5+/7 candles same dir = reject)
- COUNTER-FLOW GUARD (1.4x opposing volume = reject)
