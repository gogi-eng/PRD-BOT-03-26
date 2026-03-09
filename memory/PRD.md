# Trading Bot v8.0 — PRD

## Original Problem Statement
Переписать бот по рекомендациям из файла "Оценка 08.03.26" — чистая архитектура, один Entry Engine, один Risk Manager, добавить 5 новых фильтров, AI analyzer.

## Architecture
```
MARKET DATA → MARKET ANALYZER → ENTRY ENGINE → RISK MANAGER → EXECUTION ENGINE → POSITION MANAGER → EXIT ENGINE
```

## Core Requirements
- ONE Entry Engine (Trend + Pullback + Liquidity Sweep)
- ONE Risk Manager (position sizing + daily limits + consecutive losses)
- ONE Execution Engine
- ONE Exit Engine (ATR-based trailing + TP + SL)
- 5 фильтров: Liquidity Sweep, Funding, Correlation, Liquidation Clusters, ATR Volatility Regime
- AI signal filter (Gemini 3 Flash через Emergent Universal Key)
- Telegram управление
- Bybit v5 API

## What's Been Implemented (2026-01-26)
- **13 Python модулей** в `/app/bot/`
- Полный пайплайн: данные → анализ → вход → риск → исполнение → позиции → выход
- Все 5 рекомендованных фильтров
- AI analyzer (Gemini 3 Flash)
- Telegram controller с кнопками
- Чистый config.yaml
- **3405 строк** вместо 20000+
- **13/13 тестов пройдено (100%)**

## Tech Stack
- Python 3.11 (asyncio)
- Bybit v5 API (aiohttp)
- python-telegram-bot 20+
- emergentintegrations (Gemini 3 Flash)
- PyYAML, python-dotenv

## File Structure
```
/app/bot/
├── main.py                        # Entry point
├── config.yaml                    # Configuration
├── .env.example                   # Environment template
├── requirements.txt               # Dependencies
├── core/
│   ├── config.py                  # YAML config loader
│   ├── security.py                # Key management
│   └── live_controls.py           # Runtime controls
├── exchange/
│   └── bybit_client.py            # Bybit v5 API
├── analysis/
│   ├── market_analyzer.py         # Trend + Volatility + Regime
│   ├── liquidity_sweep.py         # Sweep detector
│   ├── funding_filter.py          # Funding rate filter
│   ├── correlation_filter.py      # Correlation filter
│   ├── liquidation_clusters.py    # Liquidation zones
│   └── ai_analyzer.py             # AI signal filter
├── engine/
│   ├── entry_engine.py            # ONE entry engine
│   ├── risk_manager.py            # ONE risk manager
│   ├── execution_engine.py        # Order execution
│   ├── position_manager.py        # Position tracking
│   └── exit_engine.py             # ATR-based exits
├── tg/
│   └── controller.py              # Telegram bot
└── utils/
    └── __init__.py                # ATR calculator
```

## Backlog (P0/P1/P2)
### P1
- Orderflow analysis (CVD, Delta, Bid/Ask imbalance)
- Backtesting framework для новой архитектуры
- WebSocket real-time data вместо polling

### P2
- Portfolio risk (max exposure, max sector)
- Multi-exchange support
- Dashboard для мониторинга
