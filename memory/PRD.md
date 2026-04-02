# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI.

## Current Status (обновлено: 2026-02-01)

### Iteration 47 — Bug Fixes (32/32 passed):
**Баги из live trading:**
1. **POSITION GONE fix** — когда `execute_close` возвращает "Position not found" 3 раза, позиция теперь финализируется как `exchange_closed` (для manual И bot). Ранее manual зацикливался навечно.
2. **Manual Sync Wider Window** — расширено окно поиска closedPnl с 5 мин до 2 часов для manual позиций. Force-finalize после 30 циклов без evidence.

### Iteration 46 — PRO Filter Integration (44/44 passed):
1. EMA Trend Guard (EMA20 vs EMA50) — отклоняет сигналы против тренда
2. Momentum Guard (close[-1] vs close[-5])
3. Volume Guard (vol < avg20 = reject)
4. EMA Trend Exit — закрытие при пересечении EMA(20)
5. Leverage снижен с 10x до 5x

### Iterations 41-45 (236/236 passed):
- Manual protection, whitelist-only, partial TP, RL v2, A/B/C grading
- Multi-TF presets, HTF ATR floors, 6 signal quality layers
- Orderflow v2, exhaustion/contra-trend/counter-flow guards

## Testing: 312/312 total
- 41: 34/34 | 42: 43/43 | 43: 69/69 | 44: 32/32 | 45: 29/29 | 46: 44/44 | 47: 32/32

## Signal Quality Filters (9 layers):
1. Exhaustion guard (5+/7 same dir)
2. Contra-trend guard (7+/10 opposing)
3. Counter-flow guard (1.4x opposing trade vol)
4. EMA trend guard (EMA20 vs EMA50)
5. Momentum guard (close[-1] vs close[-5])
6. Volume guard (vol < avg20)
7. Orderbook direction guard (1.3x bid/ask)
8. Price momentum guard (3/3 candles)
9. No-zone requires BOS or sweep

## Exit Mechanisms (6):
1. Hard SL | 2. Trailing Exit | 3. TP Cap | 4. Early Exit | 5. EMA Trend Exit | 6. Liquidation Stop

## Critical Rules
- MANUAL TRADES: trailing_exit, tp_cap, trend_exit allowed. hard_sl, early_exit blocked
- POSITION GONE: "not found" error → auto-finalize as exchange_closed
- LEVERAGE = 5x | PRESET = 1h | NO CHOP regime

## Upcoming Tasks
- (P1) /signal_log — Telegram command для просмотра отклонённых сигналов
- (P2) Grade-based sizing — A=100%, B=75%, C=50%

## Backlog
- Refactoring main.py на модульные менеджеры
- Advanced AI filter, Liquidation zones
