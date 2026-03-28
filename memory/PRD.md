# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI.

## Current Status (обновлено: 2026-03-28)

### Signal Logic Fixes (итерация 45, 29/29 passed):
**Проблема**: 80% сигналов разворачивались. TAOUSDT SHORT при бычьем рынке, без структуры.

1. **No-zone bypass ужесточён** — теперь bypass требует BOS или sweep, даже при conf>=0.85 и smc>=0.85. Чисто AI-based сигналы без структуры = REJECT.
2. **Contra-trend guard** — отклоняет если 7+/10 свечей идут ПРОТИВ сигнала (бычьи свечи при SELL).
3. **TRAILING_EXIT перед HARD_SL** — исправлен баг когда trail stop совпадал с SL и manual позиция зависала.
4. **HTF ATR floor в мониторинге** — trailing distance теперь = max(1m ATR, 15m ATR). Для TAOUSDT: $0.30 → $1.80.
5. **Orderbook direction guard** — SELL блокируется когда bid_vol >> ask_vol (покупатели доминируют).
6. **Price momentum guard** — все 3 свечи против сигнала = reject.

### Итерация 44 (32/32):
- Orderflow v2 (depth 25, absorption), exhaustion guard, counter-flow guard, trailing 0.7/1.2

### P0-P2 (итерации 41-43, 146/146):
- Manual protection, whitelist-only, partial TP 30/70, RL v2, A/B/C grading, multi-TF

## Testing: 236/236 total
- 41: 34/34 (P0) | 42: 43/43 (P1) | 43: 69/69 (P2) | 44: 32/32 (Signal v1) | 45: 29/29 (Signal v2)

## Signal Quality Filters (6 layers):
1. Entry Engine: exhaustion guard (5+/7 same dir)
2. Entry Engine: contra-trend guard (7+/10 opposing)
3. Entry Engine: counter-flow guard (1.4x opposing trade vol)
4. Main: orderbook direction guard (1.3x bid/ask imbalance)
5. Main: price momentum guard (3/3 candles against)
6. Quality Gate: no_zone requires BOS or sweep

## Critical Rules
- MANUAL TRADES ARE SACRED
- FEE AWARENESS (0.06%)
- NO ZONE WITHOUT STRUCTURE = REJECT
