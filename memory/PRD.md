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
  - добавлен отдельный **manual-safe mode** для ручных позиций:
    - RL выключен
    - трейлинг мягче, чем у сигналов бота
    - если у ручной позиции уже есть TP на бирже, бот его сохраняет и не строит partial TP поверх него
    - Telegram-логи по событиям: подхват / partial TP / перенос SL / portfolio TP
  - исправлена блокировка торговли после `EMERGENCY`: теперь `reset_guard()` и `resume()` реально снимают EMERGENCY, а `START_BOT` в Telegram возобновляет guard
  - добавлен request throttling в `BybitClient`, чтобы снизить `10006 Too many visits`
  - убрано нежелательное быстрое закрытие одиночной позиции через `portfolio_total_tp`:
    - `portfolio_tp` теперь выключен по умолчанию
    - даже если включить его обратно, он работает только при `>= 2` позициях
  - добавлена новая корзинная логика по пожеланию пользователя:
    - если открыто `2+` позиций
    - и хотя бы одна позиция ушла в минус
    - бот отслеживает пик суммарной прибыли по всем позициям аккаунта
    - и закрывает всю корзину при откате **20% от максимальной суммарной прибыли**
  - добавлен `profit_drawdown_guard` для **всех позиций**, включая ручные и сделки самого бота:
    - сопровождение прибыли и trailing не включаются раньше, чем позиция даст **+3% от входа**
    - после активации бот отслеживает пик прибыли
    - и закрывает позицию при откате **25% от накопленной прибыли от пика**
  - устранён ещё один choke point по входам:
    - `liquidation heatmap` теперь использует **адаптивный шаг кластеризации** для дешёвых монет (`0.01` для цен 1–10, `0.001` для 0.1–1 и т.д.)
    - если live liquidation cache пуст, бот строит **synthetic heatmap** по high/low последних свечей
    - если и этого недостаточно, включается **directional heatmap fallback** по голосам `trend + htf_trend + orderflow`
    - это убирает ситуацию, когда бот вообще не может открыть сделку только потому, что не получил живой liquidation target
  - реализована **Strategy v2** под текущий импульсный рынок по выбору пользователя `1.A + 2.B`:
    - RSI полностью убран из условий входа
    - AI фильтр переведён в режим **soft veto**: он блокирует только явный `fake breakout / bull trap / bear trap`
    - новый вход использует сразу 3 сценария:
      - `breakout continuation`
      - `pullback continuation`
      - `momentum continuation`
    - `SCAN SUMMARY` в конце скана показывает причины отказов по символам
    - после следующего живого лога по выбору пользователя `B` добавлена **детализация reject_reason**:
      - `no_structure`
      - `chop_without_breakout`
      - `breakout_not_confirmed_on_15m`
      - `weak_orderflow`
      - `transformer_not_confirmed`
      - `heatmap_not_confirmed`
    - пороги входа ослаблены ещё на один шаг, чтобы уменьшить `entry_filters`, но без снятия market quality filter:
      - `transformer_threshold=0.54`
      - `min_orderflow_imbalance=1.03`
      - `max_liq_distance_pct=1.30`
      - `min_rr_ratio=1.3`
      - `ai.min_confidence=52`
  - по live-логам пользователя найден дополнительный choke point: `atr_too_low=7`
    - ATR-проверка перенесена **после** построения сигнала
    - теперь low ATR больше не убивает **breakout**-входы преждевременно
    - но для `pullback`-входов ATR-контроль сохранён, как попросил пользователь (`Mode C`)
  - после замечания пользователя убрана плохая идея с symbol-specific guard
    - стратегия снова полностью **generic**, без привязки к конкретным тикерам
    - вместо заглушки введён общий `market quality filter`:
      - основной фильтр = **15m структура**
      - `1m` используется только как trigger
      - в `chop` разрешены только **confirmed breakout after range exit**
      - `pullback` и `continuation` в `chop` блокируются для любых символов
      - breakout должен подтверждаться выходом за диапазон 15m и качеством объёма/импульса
  - TP/SL для подхваченных позиций теперь рассчитываются по структуре:
    - SL = под/над уровнями ликвидности или swing level + ATR buffer
    - TP = следующий liquidity cluster / resistance / support + ATR buffer
    - fallback на RR применяется только если уровней действительно нет
  - по выбору пользователя `1.C + 2.A` добавлены HTF-структурные зоны:
    - новый модуль `analysis/structure_zones.py`
    - детектируются `15m FVG` и `15m Order Block`
    - приоритет входа/SL теперь идёт по **confluence FVG + OB**, если доступны обе зоны
    - для auto/manual позиций рассчитываются:
      - `TP1 = ближайшая ликвидность / уровень`
      - `TP2 = следующий уровень`
      - остаток удерживается через trailing / existing management
    - `partial_tp_price` для auto/manual сопровождения теперь может ставиться именно в `TP1`, а не только как % от финального TP
  - устранён критический баг `micro exits` из live Telegram-логов пользователя:
    - RL больше **не закрывает** позицию на микропрофите вроде `+0.01%`
    - RL больше **не режет** позицию на микролоссе вроде `-0.02%` без реальной причины
    - `RL CLOSE` разрешён только если прибыль уже >= `0.5%` или убыток <= `-0.6%`
    - `RL REDUCE` разрешён только если прибыль >= `0.8%`
    - для входов и подхваченных позиций введены минимальные дистанции:
      - `TP >= 1.2%`
      - `SL >= 0.35%`
    - `protective_liq_level` теперь хранит уже **финальный stop_loss**, а не сырой слишком близкий уровень ликвидности
  - параметры входа смягчены до режима **осторожно, но не мёртво**:
    - `transformer_threshold=0.60`
    - `max_liq_distance_pct=0.55`
    - `min_orderflow_imbalance=1.12`
    - `min_atr_pct=0.20`
    - `trade_symbols=6`
    - `ai.min_confidence=62`, `fail_open=true`
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
- manual-safe mode self-test `/app/backend_test.py` — **14/14 PASS**
- P0 fixes self-test `/app/backend_test.py` — **16/16 PASS**
- `testing_agent` report `/app/test_reports/iteration_4.json` — **79/79 PASS**
- profit drawdown self-test `/app/backend_test.py` — **17/17 PASS**
- `testing_agent` report `/app/test_reports/iteration_5.json` — **101/101 PASS**
- adaptive + directional heatmap self-test `/app/backend_test.py` — **20/20 PASS**
- `testing_agent` report `/app/test_reports/iteration_7.json` — **146/146 PASS**
- strategy v2 validation `/app/test_reports/iteration_8.json` — **193/193 PASS**
- low-ATR breakout exception `/app/test_reports/iteration_9.json` — **194/194 PASS**
- micro-exit prevention `/app/test_reports/iteration_10.json` — **186/186 PASS**
- major-chop guard `/app/backend_test.py` — **22/22 PASS**
- generic market quality verification `/app/test_reports/iteration_11.json` — **227/227 PASS**
- detailed reject reasons + looser filters `/app/backend_test.py` — **22/22 PASS**
- FVG/OB zone integration `/app/backend_test.py` — **22/22 PASS** (base suite remains green)

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
- Проверить на реальных данных новую корзинную логику: `2+ позиции -> одна в минус -> закрытие по 20% drawdown от пика`
- Проверить на реальных сделках новый `profit_drawdown_guard`: активация только после `+3%`, затем закрытие на `25%` откате от пика прибыли
- Проверить на реальном запуске, что после adaptive/synthetic/directional heatmap fallback бот снова начал выдавать собственные входы, особенно на дешёвых монетах
- Проверить на реальном рынке, что `SCAN SUMMARY` показывает кандидатов/отказы, а новая Strategy v2 действительно начала открывать сделки без RSI-перефильтрации
- Проверить на реальных логах, что число отказов `atr_too_low` резко упало и появились хотя бы breakout-кандидаты / реальные входы
- Проверить в live-логах, что сделки больше не закрываются на `+0.01% / -0.02%` и TP/SL реально покрывают комиссию и движение цены
- Проверить, что generic market quality filter блокирует любые `chop`-входы без breakout по всем символам, а не только по отдельным тикерам
- Проверить по live `SCAN SUMMARY`, что вместо общего `entry_filters` теперь видны конкретные bottleneck-причины и число кандидатов стало выше
- Проверить на live SOL/альтах, что входы и SL/TP теперь реально опираются на `15m FVG / OB / support-resistance`, а не на ближайший «слепой» уровень

### P1
- Усилить transformer/market regime модели реальными обученными весами вместо rule-based approximation
- Добавить отдельный лог/дамп AI predictions и heatmap snapshots в jsonl
- Добиться точной настройки capital allocator под разные классы активов

### P2
- Отдельный backtesting harness под новую архитектуру v9.0
- Portfolio-level exposure caps across symbols
- Web dashboard / observer panel
