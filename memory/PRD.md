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

### 5-Point ТЗ Implementation Status:
1. **4H Trend Filter** — DONE. EMA20 vs EMA50 + last 3 candles direction.
2. **Entry: Sweep → FVG/OB** — DONE. No other entry types allowed.
3. **early_exit_bars = 0** — DONE. Disabled.
4. **Whitelist: BTC, ETH, SOL, LINK, BNB** — DONE. No other coins.
5. **RR >= 2.0** — DONE. Config set to 2.5.

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

### New Modules Added:
- **LiquidityHeatmap** (`analysis/liquidity_heatmap.py`) — Real orderbook-based heatmap, replaces synthetic fallback. Detects bid/ask walls and calculates liquidity magnet direction.

## Testing
- iteration_14: 47/47 pass (SMC v5 — all 5 points + edge cases)
- All previous test suites maintained

## Architecture
```
/app/bot/
├── main.py                  # Orchestrator
├── config.yaml              # Configuration
├── analysis/
│   ├── market_structure.py  # Swing, BOS, sweep detection
│   ├── structure_zones.py   # FVG + Order Block zones
│   ├── liquidity_heatmap.py # NEW: Real orderbook heatmap
│   ├── orderflow_analyzer.py
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

## Backlog
- P1: Investigate heatmap_analyzer synthetic fallback (partially solved with LiquidityHeatmap)
- P2: Backtesting module
- P2: Web dashboard
- P2: User's advanced quant components (Transformer PyTorch model, RL Position Manager)
