# Trading Bot v9.0 — PRD

## Original Problem Statement
Пользователь потребовал переписать бота в строгом соответствии с новым ТЗ `ТЗ 13.03.26.txt` и выбрал режим реализации:

**A. Максимально строгое следование ТЗ без сохранения старых компромиссов.**

Цель: превратить бота в многоуровневую AI-архитектуру с новым Entry Engine, liquidation heatmap, transformer-прогнозом, market regime AI, RL-управлением позицией и allocator-логикой по символам.

## Current Architecture
```
DATA LAYER → FEATURE ENGINEERING → MARKET REGIME AI → TRANSFORMER MODEL
→ ENTRY ENGINE → CAPITAL ALLOCATOR → RISK MANAGER → EXECUTION ENGINE
→ POSITION MANAGER → RL POSITION AGENT → EXIT ENGINE → TELEGRAM CONTROL
```

## Core Requirements from ТЗ 13.03.26
- Liquidation Heatmap с кластерами и целями `max_liq_cluster_above / max_liq_cluster_below`
- Новый Entry Engine только на основе:
  - Transformer probability
  - Liquidation magnet proximity
  - Orderflow imbalance
  - Regime filter
- Market Regime AI: `TREND / CHOP / BREAKOUT`
- RL agent для действий `hold / add / reduce / close`
- Multi-symbol capital allocator c softmax weighting
- Risk per trade: **0.5% капитала**
- ATR stop + liquidation stop
- Execution: **LIMIT + POST ONLY + fallback to market**
- Telegram controls: START/STOP, risk, AI, RL, heatmap, positions
- Monitoring/logging of predictions, heatmap, signals, PnL

## What Has Been Implemented

### Previous foundation
- Полностью переписанный бот в `/app/bot` вместо большого legacy-кода
- Profit lock, Telegram interface, trade history, базовый risk management
- Bybit v5 integration, AI filter via `emergentintegrations`

### New implementation for ТЗ 13.03.26 (current fork)
- **`main.py` переписан под v9.0 AI-fund pipeline**
- Добавлены новые модули:
  - `analysis/orderflow_analyzer.py`
  - `analysis/feature_engineering.py`
  - `analysis/market_regime_ai.py`
  - `analysis/transformer_model.py`
  - `engine/capital_allocator.py`
  - `engine/rl_position_agent.py`
- Переписаны ключевые модули:
  - `analysis/market_analyzer.py`
  - `analysis/liquidation_clusters.py`
  - `analysis/ai_analyzer.py`
  - `engine/entry_engine.py`
  - `engine/execution_engine.py`
  - `engine/exit_engine.py`
  - `engine/position_manager.py`
  - `core/live_controls.py`
  - `tg/controller.py`
  - `config.yaml`
- Bybit client расширен:
  - public orderbook
  - recent trades
  - liquidation WebSocket cache
  - order status / cancel order
  - post-only limit support
- Добавлено сопровождение уже открытых позиций на аккаунте:
  - бот подхватывает **все открытые позиции аккаунта Bybit**, включая открытые вручную
  - существующие SL/TP на бирже сохраняются и не перетираются
  - если у внешней позиции SL/TP отсутствуют, бот рассчитывает и выставляет недостающие уровни
  - partial TP: закрытие **50% объёма на 50% пути к финальному TP**
  - portfolio total TP: закрытие **всех позиций аккаунта** по суммарной прибыли
  - `position_idx` протянут через close / update SL / update TP для корректной работы с подхваченными позициями
  - исправлен цикл profit lock: `None` больше не ломает `for symbol in closed_symbols`
  - для подхваченных/ручных позиций отключён `early_exit`, чтобы бот не закрывал их слишком рано этой внутренней логикой
- Новый backend smoke test suite в `/app/backend_test.py`

## Current Strategy Logic

### Entry
LONG:
- `transformer_prob_up >= 0.62`
- target heatmap above price
- distance to heatmap target `<= 0.4%`
- bullish orderflow ratio `>= 1.2`
- regime in `trend/breakout`

SHORT:
- `transformer_prob_down >= 0.62`
- target heatmap below price
- distance to heatmap target `<= 0.4%`
- bearish orderflow ratio `>= 1.2`
- regime in `trend/breakout`

### Risk & execution
- Base risk per trade: `0.5%`
- Capital allocation modifies size through symbol weight
- Orders: post-only limit first, then market fallback
- Exit stack:
  - liquidation stop
  - hard SL
  - early exit
  - trailing stop
  - TP cap
- External/manual position stack:
  - adopt all open exchange positions
  - preserve existing exchange SL/TP
  - partial TP at 50% route to final TP
  - portfolio-wide total TP on aggregate unrealized profit
- RL agent can `add / reduce / close / hold`

## Testing Status
- Ruff lint: `/app/bot` and `/app/backend_test.py` — **PASS**
- Local smoke script for synthetic pipeline — **PASS**
- `/app/backend_test.py` updated for new architecture — **13/13 PASS**
- `testing_agent` report `/app/test_reports/iteration_3.json` — **44/44 PASS**
- `deep_testing_backend_v2` verification — **PASS**

## Running Notes for User
- Бот запускается отдельно от preview-среды
- После передачи изменений пользователю нужно **скопировать весь `/app/bot` каталог целиком**
- Затем на сервере пользователя выполнить:
  - `pip install -r requirements.txt`
  - запустить уже новую версию `main.py`

## Priority Backlog

### P0
- Прогнать уже на реальном сервере пользователя с их Bybit/Telegram ключами
- Проверить реальное поведение liquidation stream, adoption ручных позиций и post-only execution на Bybit

### P1
- Усилить transformer/market regime модели реальными обученными весами вместо rule-based approximation
- Добавить отдельный лог/дамп AI predictions и heatmap snapshots в jsonl
- Добиться точной настройки capital allocator под разные классы активов

### P2
- Отдельный backtesting harness под новую архитектуру v9.0
- Portfolio-level exposure caps across symbols
- Web dashboard / observer panel
