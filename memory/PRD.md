# Trading Bot v9.0 — PRD

## Current Strategy: SMC v3.1 (Enhanced)

### Полный сигнал (два типа):
```
# Тип 1: Sweep → BOS → Retest
if trend != RANGE and liquidity_sweep and BOS and volume_spike and retest_OB:
    open_trade()

# Тип 2: Continuation
if BOS and trend_up and pullback < 0.4 ATR:
    buy()
```

### Все реализованные фичи:
1. Market Structure Engine — swing HH/HL/LH/LL тренд
2. BOS — с volume confirmation (>1.5x avg)
3. Liquidity Sweep Detection
4. Entry: Sweep → BOS → Retest OB/FVG (primary)
5. Entry: BOS → Pullback < 0.4 ATR → Continuation (secondary)
6. Order Block + FVG zones
7. SL = sweep_low - ATR*0.2, TP = previous_high / liquidity
8. Trailing: 1R→breakeven, 2R→swing low, 3R+→distance
9. Momentum Filter: volume>2x AND range>1.5x ATR
10. Pyramid: add1 at R>0.5, add2 at R>1.2, risk_total ≤ 2%
11. Risk: 0.4% per trade, 6 positions, 4% daily loss, 15x leverage
12. Execution checks: spread<0.08%, funding_rate<0.05
13. AI filter РЕАЛЬНЫЙ: reject if ai_confidence < 45
14. Volatility filter: ATR/price < 0.8% → skip
15. Scanning: 40 символов по momentum, не 8
16. Min SMC score: 0.55 (отсекает фальш-сигналы)

## Config Key Values
- risk_per_trade_pct: 0.4%
- max_positions: 6
- max_daily_loss_pct: 4%
- leverage: 15x
- trade_symbols: 40
- whitelist_enabled: false (scan all by momentum)
- ai.min_confidence: 45
- entry.min_smc_score: 0.55
- entry.min_volatility_pct: 0.8
- pyramid.add1_min_r: 0.5
- pyramid.add2_min_r: 1.2

## Testing
- iteration_12: 41/41 pass (SMC v1)
- iteration_13: 38/38 pass (SMC v3)
- Full suite: 205/205 pass (v3.1 with all new features)

## Backlog
- P1: Heatmap — заменить synthetic на реальные (OI, orderbook depth)
- P2: Backtesting harness
- P2: Web dashboard
