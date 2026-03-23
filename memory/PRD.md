# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI, с фокусом на:
- контроль качества сигналов (без flood-спама),
- рабочий pipeline обучения,
- интеграцию обученной модели в live-entry логику,
- прибыльность в live-торговле.

## Current Status (обновлено: 2026-03-27)

### Реализовано в текущей итерации (Anti-Loss Package v1)

**P0 — Критичные исправления:**
1. **Повышение порога входа**: `entry_threshold: 0.56 → 0.72` — только высококонфидентные сигналы
2. **Включение Early Exit**: `early_exit_bars: 0 → 6` — закрытие "мёртвых" позиций после 6 баров
3. **AI фильтр**: `trained_model_min_prob: 0.0 → 0.52` — модель отклоняет слабые прогнозы
4. **exchange_closed учитывается**: убран из `ignore_loss_cooldown_reasons` и `ignore_consecutive_loss_reasons`
5. **Уменьшение позиций**: `max_positions: 3 → 2`, `trade_symbols: 25 → 10`
6. **Quality gate no_zone**: `reject_no_zone_entries: true` работает в ОБОИХ режимах (LIVE + signal-only)

**P1 — Важные исправления:**
7. **Увеличение влияния AI**: `trained_model_blend: 0.10 → 0.30`
8. **Улучшение R:R**: `min_rr_ratio: 2.5 → 3.0`
9. **Увеличение кулдауна**: `cooldown_after_loss_sec: 1800 → 3600` (1 час)
10. **Шире стопы**: `min_stop_atr_mult: 1.4 → 1.6`, `sl_buffer_atr_mult: 0.8 → 1.0`
11. **Trailing stop диагностика**: добавлено детальное логирование в `exit_engine.py` и `main.py`
    - `[TRAIL ACTIVATED]` — момент активации trailing stop (breakeven)
    - `[TRAIL MOVE]` — каждое подтягивание стопа с R-multiple
    - `[TRAIL]` — диагностика каждого цикла (цена, best, R, trail_stop, activation, SL, bars)

### Ранее реализовано
1. Signal Flood Fix (threshold + same-side cooldown)
2. Полный rewrite `train_transformer.py` (BCEWithLogitsLoss, precision-ориентированный)
3. Интеграция обученных весов в live-entry
4. Signal-only Feedback Loop с auto-labeling
5. Quality Gate перед сигналом
6. LYNUSDT hotfix (контртренд-логика)
7. AI bias hotfix (direction match + uniformity guard)
8. Дебаунс exchange_closed (3 цикла + closedPnl)
9. 15-мин cooldown на символ после exchange_closed
10. Пауза синхронизации после rate-limit (180с)
11. Adaptive regime presets (trend/range)
12. Origin field в trade_history.json

## Architecture
```
/app/bot/
├── main.py              # Orchestrator
├── config.yaml          # Central configuration
├── analysis/            # Market analysis modules
├── engine/
│   ├── entry_engine.py  # Scoring + filters
│   ├── exit_engine.py   # Exit + trailing (с логированием)
│   ├── risk_manager.py  # Cooldown + risk limits
│   └── ...
├── exchange/
│   └── bybit_client.py  # Bybit v5 API
└── tg/
    └── controller.py    # Telegram commands
```

## Testing Status
- Iteration 39: 46/46 passed (Anti-Loss Package)
- All config values verified
- EntryEngine, ExitEngine, RiskGuard behaviour verified

## Prioritized Backlog

### P1 (следующие задачи)
- Whitelist-only режим (настраиваемый)
- Partial TP 30/70 логика
- `/retrain_status` Telegram команда
- Рассмотреть `adopt_all_positions: false` или строгий фильтр

### P2 (архитектурные)
- Переход на 5мин/15мин TF
- RL position manager
- A/B/C грейдинг сигналов
- Добавить фильтр волатильности ATR < 0.3%
