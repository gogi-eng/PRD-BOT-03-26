# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на базе Bybit API. Стратегия Smart Money Concepts (SMC) с AI-фильтрацией.

## Current Strategy: SMC v5 — Strict 5-Point ТЗ

### Entry Pipeline (STRICT — no other logic allowed):
```
GATE 1: 4H TREND  →  only LONG if 4H bullish, only SHORT if 4H bearish
    ↓
GATE 2: LIQUIDITY SWEEP  →  mandatory: price must sweep liquidity first
    ↓
GATE 3: FVG / ORDER BLOCK  →  price must retest a zone after the sweep
    ↓
GATE 4: RISK/REWARD >= 2.0  →  trade only if RR meets minimum
    ↓
ENTRY
```

### 5-Point ТЗ Implementation: ALL DONE
1. **4H Trend Filter** — EMA20 vs EMA50 + last 3 candles direction
2. **Entry: Sweep → FVG/OB** — No other entry types allowed
3. **early_exit_bars = 0** — Disabled
4. **Whitelist: BTC, ETH, SOL, LINK, BNB** — No other coins
5. **RR >= 2.0** — Config set to 2.5

### Key Config Values:
- risk_per_trade_pct: 0.4%
- max_positions: 6
- max_daily_loss_pct: 4%
- leverage: 15x
- min_rr_ratio: 2.5
- early_exit_bars: 0
- whitelist_enabled: true
- whitelist_symbols: BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT, BNBUSDT
- ai.min_confidence: 55
- signal_only: true

## Quant Modules (Feb 2026)

### New/Enhanced:
- **LiquidityHeatmap** (`analysis/liquidity_heatmap.py`) — Real orderbook-based heatmap (Coinglass-style)
- **RegimeDetector** (`analysis/market_regime_ai.py`) — Cleaner regime detection: ADX + volatility + compression + HTF alignment
- **OrderflowImbalance** — Normalized `(Buy-Sell)/(Buy+Sell)` added to OrderflowAnalyzer
- **FeatureBuilder** — Enhanced with 15 features (added normalized_imbalance)
- **Backtester** (`bot/backtester.py`) — Walk-forward backtesting on historical Bybit data

### Backtester Usage:
```bash
python -m bot.backtester --symbol BTCUSDT --days 14
python -m bot.backtester --all-whitelist --days 30
python -m bot.backtester --all-whitelist --days 7 --interval 5
```

## Architecture
```
/app/bot/
├── main.py                  # Orchestrator
├── config.yaml              # Configuration
├── backtester.py            # NEW: Walk-forward backtesting
├── analysis/
│   ├── market_structure.py  # Swing, BOS, sweep detection
│   ├── structure_zones.py   # FVG + Order Block zones
│   ├── liquidity_heatmap.py # Real orderbook heatmap
│   ├── market_regime_ai.py  # ENHANCED: Regime detector
│   ├── orderflow_analyzer.py # ENHANCED: +normalized_imbalance
│   ├── feature_engineering.py # ENHANCED: 15 features
│   ├── transformer_model.py
│   └── ...
├── engine/
│   ├── entry_engine.py      # v5: Strict 4-gate pipeline
│   ├── exit_engine.py
│   ├── position_manager.py
│   └── ...
├── exchange/
│   └── bybit_client.py
└── tg/
    └── controller.py
```

## Testing
- iteration_14: 47/47 pass (SMC v5 — all 5 points + edge cases)
- quant_modules: 23/23 pass (regime, orderflow, features, backtester)
- Full suite: 275/275 pass

## Backlog
- P1: Run backtester on all whitelist symbols, analyze results
- P2: PyTorch Transformer + RL Agent (needs training data from backtester)
- P2: Web dashboard
- P2: Telegram /stats command for live performance tracking
