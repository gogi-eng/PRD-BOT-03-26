# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI.

## Current Status (обновлено: 2026-03-28)

### P1 Features (итерация 42, 43/43 passed):
1. **Whitelist-only режим** — `whitelist_only: true/false` в `config.yaml` → market. Если true, бот торгует ТОЛЬКО монеты из whitelist, пропуская сканирование рынка.
2. **Partial TP 30/70** — `close_fraction: 0.3` (было 0.5). Закрывает 30% позиции на первом TP, 70% продолжает работать. Лейбл динамический: `partial_tp_30pct`.
3. **/retrain_status** — Telegram команда показывает прогресс авто-ретрейна: качественные метки, прогресс-бар, размер датасета, даты последнего ретрейна.

### P0 Manual Position Protection (итерация 41, 34/34 passed):
1. **force_closed_stale ЗАБЛОКИРОВАН для manual** — бот НЕ удаляет ручные позиции после 3 неудачных закрытий. Telegram-алерт.
2. **HARD_SL ЗАБЛОКИРОВАН для manual** — только TRAILING_EXIT и TP_CAP разрешены для ручных.
3. **LIQUIDATION_STOP ЗАБЛОКИРОВАН для manual** — protective_liq_level=0.0 + ExitReason guard.
4. **EARLY_EXIT ЗАБЛОКИРОВАН для manual** — allow_early_exit=False.
5. **early_exit_bars: 6 → 20** — больше времени перед early_exit.

### Предыдущие итерации
- Anti-Loss Package v1 (итерация 39, 46/46 passed)
- Critical Bug Fix — exchange_closed + manual protection (итерация 40, 34/34 passed)
- Fee-aware trailing stops, momentum guards, zombie recovery, profit_lock tuning

## Architecture
```
/app/bot/
├── main.py              # Orchestrator (~2467 lines)
├── config.yaml          # Central configuration
├── CONFIG_GUIDE.md      # Config documentation
├── engine/
│   ├── entry_engine.py  # Scoring + filters
│   ├── exit_engine.py   # Exit + trailing (fee-aware)
│   ├── risk_manager.py  # Cooldown + risk limits
│   ├── signal_feedback_loop.py  # Feedback + retrain status
│   └── ...
├── exchange/
│   └── bybit_client.py  # Bybit v5 API
└── tg/
    └── controller.py    # Telegram commands (+/retrain_status)
```

## Testing Status
- Iteration 39: 46/46 passed (Anti-Loss Package)
- Iteration 40: 34/34 passed (Manual position protection + exchange_closed fix)
- Iteration 41: 34/34 passed (P0 Manual Position Protection)
- Iteration 42: 43/43 passed (P1 Features: whitelist-only, partial TP 30/70, /retrain_status)

## Prioritized Backlog

### P2
- RL position manager
- A/B/C грейдинг сигналов
- Переход на 5мин/15мин TF

## Config Changes Summary
```yaml
# P0
exit.early_exit_bars: 6 → 20

# P1
market.whitelist_only: false (NEW)
partial_tp.close_fraction: 0.5 → 0.3
```

## Critical Rules
- **MANUAL TRADES ARE SACRED**: Бот НЕ закрывает manual позиции по HARD_SL, LIQUIDATION_STOP, EARLY_EXIT, force_closed_stale
- **FEE AWARENESS**: Bybit linear taker fees (0.06%) учитываются в trailing breakeven
- **LANGUAGE**: Ответы ТОЛЬКО на русском
