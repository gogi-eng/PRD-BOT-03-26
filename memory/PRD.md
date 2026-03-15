# Trading Bot v9.0 — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют через Bybit. Пользователь и его коллега разработали ТЗ на 13 пунктов, основанное на Smart Money Concepts (SMC).

## Current Strategy: SMC v3 (Sweep → BOS → Retest)

### Полный сигнал:
```
if trend != RANGE
and liquidity_sweep
and BOS
and volume_spike
and retest_order_block:
    open_trade()
```

### 13 пунктов ТЗ (все реализованы):
1. Market Structure Engine — swing HH/HL/LH/LL тренд
2. Break of Structure (BOS) — с подтверждением объёмом (>1.5x avg)
3. Liquidity Sweep Detection — wick за swing, close обратно
4. Entry Logic — Sweep → BOS → Retest OB/FVG
5. Order Block — последняя противоположная свеча перед импульсом
6. Fair Value Gap — entry_zone = midpoint(FVG)
7. SL = sweep_low - ATR*0.2, TP = previous_high / liquidity
8. Trailing: 1R → breakeven, 2R → last swing low, 3R+ → distance
9. Momentum Filter: volume > 2x avg AND range > 1.5x ATR
10. Pyramid Strategy: entry1=breakout, entry2=pullback, entry3=continuation, risk_total ≤ 2%
11. Risk Manager: 0.5% risk, 5 max positions, 5% daily loss, 15x leverage
12. Execution checks: spread < 0.08%, funding_rate < 0.05
13. Full signal logic: trend≠RANGE + sweep + BOS + momentum + retest_OB

## Architecture
```
/app/bot/
├── main.py                    # Orchestrator with pyramid + basket guard
├── config.yaml                # All parameters
├── analysis/
│   ├── market_structure.py    # NEW: Swing/BOS/Sweep/Momentum engine
│   ├── structure_zones.py     # FVG + Order Block detection
│   ├── market_analyzer.py     # EMA/RSI/ADX analysis
│   ├── ai_analyzer.py         # Gemini AI filter
│   └── ...
├── engine/
│   ├── entry_engine.py        # SMC v3 entry: Sweep→BOS→Retest
│   ├── exit_engine.py         # R-based trailing (1R/2R/3R)
│   ├── risk_manager.py        # Risk/position sizing
│   ├── execution_engine.py    # Bybit order placement
│   └── position_manager.py    # Position state
└── tg/controller.py           # Telegram interface
```

## Testing
- iteration_12: 41/41 SMC v1 tests pass
- iteration_13: 38/38 SMC v3 tests pass (all 13 features)
- Full test suite: 167/167 pass

## Backlog
- P0: Live testing on server — verify sweep/BOS signals, R-based trailing, pyramid adds
- P1: Heatmap synthetic data fallback improvement
- P2: Backtesting harness
- P2: Web dashboard
