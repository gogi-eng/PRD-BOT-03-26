# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI.

## Current Status (обновлено: 2026-02-01)

### Iteration 46 — PRO Filter Integration (44/44 passed):
**Задача**: Интеграция PRO-концепций (EMA тренд, импульс, объём, trend exit) в существующую систему.

1. **EMA Trend Guard** — отклоняет LONG если EMA(20) < EMA(50), SELL если EMA(20) > EMA(50)
2. **Momentum Guard** — отклоняет если close[-1] vs close[-5] противоречит сигналу
3. **Volume Guard** — отклоняет если текущий объём < среднего за 20 свечей
4. **EMA Trend Exit** — закрывает позицию при пересечении EMA(20) против направления (после ema_period баров)
5. **Leverage снижен** с 10x до 5x
6. **Config fixes**: chop убран из allowed_regimes, cooldown исправлен

### Iteration 45 — Signal Logic Fixes (29/29 passed):
1. No-zone bypass ужесточён — bypass требует BOS или sweep
2. Contra-trend guard — 7+/10 свечей ПРОТИВ = REJECT
3. TRAILING_EXIT перед HARD_SL — исправлен баг
4. HTF ATR floor — trailing distance = max(1m ATR, 15m ATR)
5. Orderbook direction guard — блокировка при доминировании покупателей/продавцов
6. Price momentum guard — 3 свечи против = reject

### P0-P2 (итерации 41-44, 178/178):
- Manual protection, whitelist-only, partial TP 30/70, RL v2, A/B/C grading, multi-TF
- Orderflow v2, exhaustion guard, counter-flow guard

## Testing: 280/280 total
- 41: 34/34 | 42: 43/43 | 43: 69/69 | 44: 32/32 | 45: 29/29 | 46: 44/44

## Signal Quality Filters (9 layers):
1. Entry Engine: exhaustion guard (5+/7 same dir)
2. Entry Engine: contra-trend guard (7+/10 opposing)
3. Entry Engine: counter-flow guard (1.4x opposing trade vol)
4. Entry Engine: EMA trend guard (EMA20 vs EMA50)
5. Entry Engine: momentum guard (close[-1] vs close[-5])
6. Entry Engine: volume guard (vol < avg20 = reject)
7. Main: orderbook direction guard (1.3x bid/ask imbalance)
8. Main: price momentum guard (3/3 candles against)
9. Quality Gate: no_zone requires BOS or sweep

## Exit Mechanisms (6):
1. Hard SL (ATR-based)
2. Trailing Exit (R-based + distance)
3. TP Cap
4. Early Exit (dead trades)
5. EMA Trend Exit (price vs EMA20)
6. Liquidation Stop

## Critical Rules
- MANUAL TRADES ARE SACRED (TREND_EXIT allowed for manual)
- FEE AWARENESS (0.06%)
- NO ZONE WITHOUT STRUCTURE = REJECT
- LEVERAGE = 5x (reduced from 10x)
- ACTIVE PRESET = 1h

## Upcoming Tasks
- (P1) /signal_log Telegram command — показ последних 10 отклонённых сигналов
- (P2) Grade-based sizing — Grade A=100%, B=75%, C=50%

## Future/Backlog
- Refactoring main.py (~2500 строк) на модульные менеджеры
- Orderflow delta improvements
- Liquidation zone integration
- Advanced AI filter
