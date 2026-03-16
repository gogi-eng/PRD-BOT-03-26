# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на базе Bybit API. Стратегия Smart Money Concepts (SMC) с AI-фильтрацией.

## Current Strategy: Entry Engine v6 — Weighted Scoring Model

### Scoring Pipeline:
```
Trend Score       × 0.30  (4H trend + sweep alignment + struct trend)
Orderflow Score   × 0.30  (normalized imbalance [-1, +1])
AI/Transformer    × 0.40  (calibrated probabilities, no 100%)
─────────────────────────
Composite Score → must be >= 0.70

Hard requirements: spread < 0.08%, funding < 5%, RR >= 2.0
```

### Key Changes from v5 → v6:
- **Weighted voting** instead of hard reject gates
- **4H trend** contributes to score (not auto-reject when neutral)
- **Orderflow** uses `(Buy-Sell)/(Buy+Sell)` normalized [-1, +1]
- **Transformer** calibrated with sigmoid (max 85%, min 5% — no 100% outputs)
- **Symbol scanner** scans top 25 by momentum (whitelist at front, not exclusive)
- **Quasi-liquidation model** replaces synthetic fallback (ATR + leverage zones + swing clusters)

### Config Values:
- trade_symbols: 25 (momentum-ranked, whitelist priority)
- entry_threshold: 0.70
- min_rr_ratio: 2.5
- early_exit_bars: 0
- whitelist_enabled: true (priority, not exclusive)
- whitelist_symbols: BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT, BNBUSDT

## Architecture
```
/app/bot/
├── main.py                  # Orchestrator
├── config.yaml              # Configuration
├── backtester.py            # Walk-forward backtesting
├── analysis/
│   ├── market_structure.py
│   ├── structure_zones.py
│   ├── liquidity_heatmap.py # Real orderbook heatmap
│   ├── market_regime_ai.py  # ADX + volatility regime detector
│   ├── orderflow_analyzer.py # +normalized_imbalance
│   ├── feature_engineering.py # 15 features
│   ├── transformer_model.py  # +sigmoid calibration
│   └── ...
├── engine/
│   ├── entry_engine.py      # v6: Weighted scoring
│   ├── exit_engine.py
│   └── ...
├── exchange/
│   └── bybit_client.py
└── tg/
    └── controller.py
```

## Testing
- Full suite: 263/263 pass (0 failures)
- entry_engine_v6: 16 tests
- smc_strategy: 22 tests (weighted scoring + config + heatmap)
- quant_modules: 23 tests (regime, orderflow, features, backtester)

## Backlog
- P1: Run backtester on all whitelist symbols, analyze results
- P2: PyTorch Transformer + RL Agent (needs training data)
- P2: Web dashboard
- P2: Telegram /stats command
