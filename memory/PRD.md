# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI.

## Current Status (обновлено: 2026-03-28)

### P0 Manual Position Protection (итерация 41, 34/34 passed):
1. **force_closed_stale ЗАБЛОКИРОВАН для manual** — бот больше НЕ удаляет ручные позиции после 3 неудачных попыток закрытия. Вместо этого сбрасывает счётчик и отправляет Telegram-алерт.
2. **HARD_SL ЗАБЛОКИРОВАН для manual** — бот не закрывает ручные позиции по своему рассчитанному stop-loss. Только TRAILING_EXIT и TP_CAP разрешены для ручных позиций.
3. **LIQUIDATION_STOP ЗАБЛОКИРОВАН для manual** — protective_liq_level=0.0 для ручных + доп. guard через ExitReason.
4. **EARLY_EXIT ЗАБЛОКИРОВАН для manual** — allow_early_exit=False для ручных.
5. **early_exit_bars: 6 → 20** — позициям даётся больше времени перед early_exit (20 баров вместо 6).

### Предыдущие итерации

**Anti-Loss Package v1 (итерация 39, 46/46 passed):**
- entry_threshold: 0.56 → 0.72
- trained_model_min_prob: 0.0 → 0.52, trained_model_blend: 0.10 → 0.30
- min_rr_ratio: 2.5 → 3.0
- max_positions: 3 → 2, trade_symbols: 25 → 10
- cooldown_after_loss_sec: 1800 → 3600
- min_stop_atr_mult: 1.4 → 1.6, sl_buffer_atr_mult: 0.8 → 1.0

**Hotfix: volatility_floor:**
- volatility_floor_atr_pct: 0.20 → 0.06 (для 1-мин TF)

**Critical Bug Fix (итерация 40, 34/34 passed):**
1. Manual positions no longer get liquidation_stop
2. exchange_closed closedPnl timestamp validation (макс. 5 мин)
3. exchange_closed_confirm_cycles: 3 → 5
4. exchange_closed_force_cycles: 8 → 20

**Other fixes (pre-iteration):**
- Fee-aware trailing stops (fee_rate: 0.0006, 2.5x buffer)
- Pre-execution Momentum Guard
- Zombie position recovery (3 failed closes → force remove for bot only)
- profit_lock threshold 5% → 25%, manual_trailing_activation_atr → 1.0

## Architecture
```
/app/bot/
├── main.py              # Orchestrator (~2450 lines)
├── config.yaml          # Central configuration
├── CONFIG_GUIDE.md      # Config documentation
├── engine/
│   ├── entry_engine.py  # Scoring + filters
│   ├── exit_engine.py   # Exit + trailing (fee-aware)
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
- Iteration 41: 34/34 passed (P0 Manual Position Protection — force_closed_stale, HARD_SL, early_exit)

## Prioritized Backlog

### P1
- Whitelist-only режим (configurable)
- Partial TP 30/70 логика
- `/retrain_status` команда Telegram
- adopt_all_positions: рассмотреть false или фильтр

### P2
- Переход на 5мин/15мин TF
- RL position manager
- A/B/C грейдинг сигналов

## Critical Rules
- **MANUAL TRADES ARE SACRED**: Бот НЕ закрывает manual позиции по HARD_SL, LIQUIDATION_STOP, EARLY_EXIT, force_closed_stale
- **FEE AWARENESS**: Bybit linear taker fees (0.06%) учитываются в trailing breakeven
- **LANGUAGE**: Ответы ТОЛЬКО на русском
