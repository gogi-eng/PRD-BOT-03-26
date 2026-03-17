# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на базе Bybit API. Стратегия Smart Money Concepts (SMC) с AI-фильтрацией.

## Current Strategy: Entry Engine v6 — Weighted Scoring Model

### Scoring Pipeline:
```
Trend Score       × 0.40  (4H trend + sweep alignment + struct trend)
Orderflow Score   × 0.35  (normalized imbalance [-1, +1])
AI/Transformer    × 0.25  (calibrated probabilities, max 85%)
─────────────────────────
Composite Score → must be >= 0.65
Hard requirements: spread < 0.08%, funding < 5%, RR >= 2.5
```

### Backtest Results (180 days, with fees):
| Symbol | Trades | Win% | PnL | PF |
|--------|--------|------|-----|-----|
| BTC | 45/81 | 33/22% | +34/+27% | 1.70/1.43 |
| ETH | 41/17 | 24/47% | +50/+85% | 1.91/7.23 |
| SOL | 34/58 | 29/29% | +45/+72% | 1.71/2.10 |
| LINK | 17/36 | 35/22% | +82/+42% | 4.99/1.86 |
| BNB | 16/63 | 44/29% | +54/+64% | 3.97/2.28 |
| **TOTAL** | **153/255** | **31/27%** | **+266/+290%** | - |

(15min / 5min intervals)

### Config Values:
- trade_symbols: 25 (momentum-ranked, whitelist priority)
- entry_threshold: 0.65
- min_rr_ratio: 2.5
- early_exit_bars: 0
- whitelist_enabled: true
- whitelist_symbols: BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT, BNBUSDT

## Architecture
```
/app/bot/
├── main.py                    # Orchestrator
├── config.yaml                # Configuration
├── backtester.py              # Walk-forward backtesting (with fees/slippage)
├── train_transformer.py       # PyTorch training pipeline
├── analysis/
│   ├── market_structure.py
│   ├── structure_zones.py
│   ├── liquidity_heatmap.py   # Real orderbook heatmap
│   ├── market_regime_ai.py    # Regime detector
│   ├── orderflow_analyzer.py  # +normalized_imbalance
│   ├── feature_engineering.py # 15 features
│   ├── transformer_model.py   # +sigmoid calibration
│   └── ...
├── engine/
│   ├── entry_engine.py        # v6: Weighted scoring
│   ├── exit_engine.py
│   └── ...
├── exchange/
│   └── bybit_client.py        # +exponential backoff
└── tg/
    └── controller.py
```

## Testing
- Full suite: 263/263 pass
- Training pipeline verified with PyTorch 2.10

## Backlog
- P1: Train Transformer on real backtest data (408 trades collected)
- P1: Integrate trained weights into live bot
- P2: RL Position Manager training
- P2: Web dashboard
