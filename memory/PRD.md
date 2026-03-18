# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI, с фокусом на:
- контроль качества сигналов (без flood-спама),
- рабочий pipeline обучения,
- интеграцию обученной модели в live-entry логику.

## Current Status (обновлено: 2026-03-18)

### ✅ Реализовано в этой итерации
1. **P0: Signal Flood Fix**
   - `entry.entry_threshold` зафиксирован на `0.85`.
   - Добавлен **same-side cooldown 1 час** на символ:
     - `bot.signal_cooldown_sec: 3600`
     - блокируются повторные `BUY→BUY` или `SELL→SELL` сигналы по одному символу.

2. **P1: Полный rewrite `train_transformer.py`**
   - Loss: `BCEWithLogitsLoss(pos_weight=...)`.
   - Модель: компактный `TinyTransformerClassifier` (малый размер).
   - Дисбаланс классов:
     - `WeightedRandomSampler`
     - win-augmentation с шумом.
   - Валидация и чекпоинт по метрике пользователя:
     - **primary = precision по классу win**
     - tie-breaker = F1.

3. **Интеграция обученных весов в live-entry**
   - `engine/entry_engine.py` теперь умеет грузить `transformer_weights.pt`.
   - Добавлены конфиги:
     - `entry.trained_model_enabled`
     - `entry.trained_model_min_prob`
     - `entry.trained_model_blend`
     - `entry.trained_model_weights_path`
   - При наличии чекпоинта:
     - рассчитывается `trained_model_prob`
     - вход отклоняется, если prob ниже `trained_model_min_prob`
     - confidence блендится: `composite*(1-blend)+trained_prob*blend`.

4. **Операционная прозрачность**
   - В startup-логах `main.py` добавлен явный статус:
     - threshold,
     - cooldown,
     - ON/OFF trained model gate.
   - Добавлен документ:
     - `/app/bot/TRAINING_AND_MODEL_INTEGRATION.md`

5. **Signal-only Feedback Loop (новое)**
   - Добавлен модуль: `engine/signal_feedback_loop.py`.
   - В signal-only режиме каждый отправленный сигнал теперь:
     - ставится в очередь наблюдения,
     - автоматически размечается как `win/loss` по `SL/TP` или по timeout,
     - добавляется в `training_data.json` как `source=signal_only_feedback`.
   - Добавлен daily retrain gate:
     - `feedback_loop.retrain_daily`
     - `feedback_loop.retrain_hour_utc`
     - `feedback_loop.min_new_labels_for_retrain`.
   - После успешного daily retrain бот автоматически перезагружает веса в `EntryEngine`.

6. **Дополнительные точечные правки**
   - `analysis/ai_analyzer.py`: кэш увеличен до `600s`.
   - `engine/entry_engine.py`: добавлена валидация некорректных SL:
     - `invalid_sl_long`
     - `invalid_sl_short`.
   - `main.py`: синхронизация `LiveControls` с config по `leverage/max_positions` подтверждена.

## Strategy Snapshot (v6 + trained gate)
```
Trend Score       × 0.40
Orderflow Score   × 0.35
AI/Transformer    × 0.25
────────────────────────
Composite Score >= 0.85

Дополнительно (если checkpoint загружен):
- trained_model_prob >= trained_model_min_prob
- blended confidence для capital score

Hard filters: spread, funding, RR >= min_rr_ratio
```

## Architecture (актуальная)
```
/app/bot/
├── main.py
├── config.yaml
├── backtester.py
├── train_transformer.py
├── TRAINING_AND_MODEL_INTEGRATION.md
├── analysis/
├── engine/
│   └── entry_engine.py   # + trained checkpoint loading + probability gate
├── exchange/
└── tg/
```

## Testing Status
- Локальный smoke по обучению: `train_transformer.py` успешно обучается на synthetic dataset и сохраняет веса.
- Pytest:
  - `/app/backend/tests/test_trained_model_integration.py`
  - `/app/backend/tests/test_entry_engine_v6.py`
  - `/app/backend/tests/test_signal_feedback_loop_and_sl_validation.py`
  - **50/50 passed** (локальный запуск).
- Testing-agent report: `/app/test_reports/iteration_16.json` — **85/85 backend passed**.

## Prioritized Backlog

### P0 (следующее действие пользователя)
- На сервере пользователя запустить новое обучение на реальном `training_data.json` и получить свежий `transformer_weights.pt`.
- Проверить первые auto-labeled сигналы в `signal_feedback_queue.json`/`training_data.json` и убедиться, что daily retrain запускается в заданный UTC-час.

### P1
- Онлайн-калибровка `trained_model_min_prob` / `trained_model_blend` по live-статистике (precision/recall на win).
- Добавить журнал качества сигналов до/после trained gate (daily summary).

### P2
- RL position manager (после накопления достаточного датасета).
- Улучшение liquidation data источника (уменьшить fallback-зависимость).
