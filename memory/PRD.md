# Trading Bot v9.0 — PRD

## Original Problem Statement
Пользователь потребовал переписать бота в строгом соответствии с новым ТЗ `ТЗ 13.03.26.txt`:
**A. Максимально строгое следование ТЗ без сохранения старых компромиссов.**

Затем пользователь потребовал перейти на стратегию Smart Money Concepts (SMC):
- Вход по FVG (Fair Value Gap) и Order Block зонам
- SL/TP по структуре рынка, а не произвольным ATR множителям
- Отключить RL exit model (rl_close создавал хаос)
- 15-минутный таймер подтверждения перед закрытием корзины позиций

## Current Architecture
```
DATA LAYER → STRUCTURE ZONE ANALYZER (FVG + OB) → FEATURE ENGINEERING
→ MARKET REGIME AI → SMC ENTRY ENGINE → CAPITAL ALLOCATOR
→ RISK MANAGER → EXECUTION ENGINE → POSITION MANAGER
→ EXIT ENGINE (structural SL/TP) → TELEGRAM CONTROL
```

## Core Strategy (SMC v1.0)

### Entry Logic
1. **Primary signal**: Price touches or enters a bullish FVG / Order Block zone (for longs), bearish zone (for shorts)
2. **Confluence scoring**: FVG + OB + HTF trend alignment = highest confidence
3. **Soft boosters** (NOT hard gates): Transformer probability, Orderflow imbalance, Heatmap proximity
4. **Breakout supplement**: Classic breakout detection as additional entry scenario
5. **Minimum entry score**: 0.40 (SMC score + boosters)

### Exit Logic
- **SL**: Placed below nearest bullish OB/FVG/swing low (for longs) with ATR buffer
- **TP**: Next resistance/liquidity zone or opposing FVG
- **Trailing**: Activates only after min_profit_before_trail_pct (0.5%) — covers commissions
- **Early exit**: Dead trades closed after 12 bars of no movement
- **RL**: DISABLED (rl.enabled: false)

### Basket Guard (15-min timer)
- When 3+ positions open and one starts dropping significantly
- Timer starts on first drawdown detection
- Only closes after drawdown persists for 15 minutes (drawdown_confirm_sec: 900)
- Timer resets if drawdown resolves before 15 minutes

## What Has Been Implemented

### SMC Strategy (Feb 2026)
- **`analysis/structure_zones.py`**: Complete rewrite with:
  - FVG detection (bullish and bearish) with gap size scoring
  - Order Block detection with displacement scoring
  - Mitigation tracking — zones "filled" by price are excluded
  - Freshness weighting (recent zones prioritized)
  - Swing high/low detection for support/resistance
  - ZoneContext with structural SL/TP helpers
  - Zone proximity methods (price_in_zone, price_near_zone)

- **`engine/entry_engine.py`**: Complete rewrite to SMC-based:
  - _smc_long_score / _smc_short_score for zone-based scoring
  - _compute_boost for transformer/orderflow/heatmap soft signals
  - Breakout detection as supplementary entry
  - Structural SL/TP from ZoneContext
  - min_rr_ratio, min_stop_distance, min_target_profit enforced

- **`engine/exit_engine.py`**: Updated with:
  - min_profit_before_trail_pct to prevent micro-exits
  - Commission-aware trailing activation
  - Structural SL/TP (from entry engine) preserved

- **`main.py`**: Updated:
  - RL close/reduce disabled (rl.enabled: false)
  - Basket guard with 15-minute confirmation timer
  - Removed _passes_market_quality_filter (SMC handles this)
  - Simplified _analyze_symbol flow
  - SMC zone context passed throughout pipeline

- **`config.yaml`**: Updated:
  - rl.enabled: false
  - zone_proximity_pct: 0.4
  - min_profit_before_trail_pct: 0.5
  - drawdown_confirm_sec: 900
  - early_exit_bars: 12

### Previous foundation
- Полностью переписанный бот в `/app/bot`
- Profit lock, Telegram interface, trade history, risk management
- Bybit v5 integration, AI filter via emergentintegrations
- Manual position adoption and management
- Profit drawdown guard (+3% activation, 25% retrace)
- Adaptive heatmap fallback for cheap coins

## Testing Status
- `testing_agent` iteration_12: **41/41 PASS** (all SMC feature tests)
- Full test suite: **167/167 PASS** (all tests green)

## Priority Backlog

### P0
- Проверить на реальном сервере: входы действительно опираются на FVG/OB зоны
- Проверить что SCAN SUMMARY показывает SMC-причины отказов
- Проверить 15-минутный таймер корзинной логики на live

### P1
- Усилить transformer/market regime модели реальными весами
- Добавить jsonl лог AI predictions и heatmap snapshots
- Настроить capital allocator под разные классы активов

### P2
- Backtesting harness под SMC стратегию
- Portfolio-level exposure caps across symbols
- Web dashboard / observer panel

## Running Notes for User
- Бот запускается отдельно от preview-среды
- После передачи скопировать `/app/bot` целиком
- На сервере: `pip install -r requirements.txt` и запустить `main.py`
