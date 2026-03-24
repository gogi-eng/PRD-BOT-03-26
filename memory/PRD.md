# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI.

## Current Status (обновлено: 2026-03-27)

### Реализовано в текущей итерации

**Anti-Loss Package v1 (итерация 39, 46/46 passed):**
- entry_threshold: 0.56 → 0.72
- early_exit_bars: 0 → 6
- trained_model_min_prob: 0.0 → 0.52
- trained_model_blend: 0.10 → 0.30
- min_rr_ratio: 2.5 → 3.0
- max_positions: 3 → 2, trade_symbols: 25 → 10
- cooldown_after_loss_sec: 1800 → 3600
- exchange_closed убран из ignore-списков
- min_stop_atr_mult: 1.4 → 1.6, sl_buffer_atr_mult: 0.8 → 1.0
- Trailing stop детальное логирование

**Hotfix: volatility_floor (по запросу):**
- volatility_floor_atr_pct: 0.20 → 0.06 (для 1-мин TF)

**Critical Bug Fix (итерация 40, 34/34 passed):**
1. **Manual positions no longer get liquidation_stop** — `protective_liq_level=0.0` для adopted позиций. Бот больше не закрывает пользовательские позиции по своим защитным уровням.
2. **exchange_closed closedPnl timestamp validation** — Бот теперь проверяет время closedPnl записей (макс. 5 мин). Старые записи от предыдущих сделок больше не считаются доказательством закрытия текущей позиции.
3. **exchange_closed_confirm_cycles: 3 → 5** — больше циклов для подтверждения
4. **exchange_closed_force_cycles: 8 → 20** — намного дольше ждёт перед force-close

## Architecture
```
/app/bot/
├── main.py              # Orchestrator
├── config.yaml          # Central configuration
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
- Iteration 40: 34/34 passed (Manual position protection + exchange_closed fix)

## Prioritized Backlog

### P1
- Whitelist-only режим
- Partial TP 30/70 логика
- `/retrain_status` команда Telegram
- adopt_all_positions: рассмотреть false или фильтр

### P2
- Переход на 5мин/15мин TF
- RL position manager
- A/B/C грейдинг сигналов
