# Trading Bot — PRD

## Original Problem Statement
Автоматический торговый бот для криптовалют на Bybit API со стратегией SMC + AI.

## Current Status (обновлено: 2026-03-28)

### P2 Features (итерация 43, 69/69 passed):

**1. RL Position Manager v2:**
- Regime-aware: адаптивные пороги для trend/breakout/chop
- Новое действие TIGHTEN — подтягивает trailing stop ближе при неблагоприятном движении
- Age penalty: чем дольше позиция удерживается, тем консервативнее управление
- Drawdown from peak tracking: учитывает просадку от максимума прибыли
- 5 действий: HOLD, ADD, REDUCE, CLOSE, TIGHTEN

**2. A/B/C Signal Grading:**
- Grade A: conf >= 0.85, RR >= 4.0, 3+ подтверждения (sweep+BOS+HTF+zone)
- Grade B: conf >= 0.75, RR >= 3.0, 2+ подтверждения
- Grade C: всё остальное, что прошло entry threshold
- Грейд в Telegram: `SIGNAL LONG [A]`, в логах и metadata

**3. Multi-TF Support (1m/5m/15m):**
- config.yaml: `tf_presets.active_preset: "1m"` (или "5m", "15m")
- Каждый пресет: candle_interval, htf, cycle_sleep, early_exit_bars, trailing params, volatility floor
- Метод `_apply_tf_preset()` переопределяет все настройки при старте
- `/tf` Telegram команда — показывает текущий TF и настройки

### P1 Features (итерация 42, 43/43 passed):
- Whitelist-only режим (`whitelist_only: true/false`)
- Partial TP 30/70 (`close_fraction: 0.3`)
- `/retrain_status` Telegram команда

### P0 Manual Position Protection (итерация 41, 34/34 passed):
- Manual позиции защищены от force_closed_stale, HARD_SL, LIQUIDATION_STOP, EARLY_EXIT
- early_exit_bars: 6 → 20

## Architecture
```
/app/bot/
├── main.py                    # Orchestrator (~2530 lines)
├── config.yaml                # Central config (+ tf_presets)
├── CONFIG_GUIDE.md            # Config documentation
├── engine/
│   ├── entry_engine.py        # Scoring + A/B/C grading
│   ├── exit_engine.py         # Exit + trailing (fee-aware)
│   ├── risk_manager.py        # Cooldown + risk limits
│   ├── rl_position_agent.py   # RL v2: regime-aware + TIGHTEN
│   ├── signal_feedback_loop.py # Feedback + retrain status
│   └── ...
├── exchange/
│   └── bybit_client.py        # Bybit v5 API
└── tg/
    └── controller.py          # Telegram (+/retrain_status, /tf)
```

## Testing Status
- Iteration 39: 46/46 passed (Anti-Loss Package)
- Iteration 40: 34/34 passed (exchange_closed + manual protection)
- Iteration 41: 34/34 passed (P0 Manual Protection)
- Iteration 42: 43/43 passed (P1 Features)
- **Iteration 43: 69/69 passed (P2 Features + regression)**

## Telegram Commands
- `/start` `/menu` — Главное меню
- `/stats` — Статистика
- `/balance` — Баланс
- `/profitlock` — Portfolio Profit Lock
- `/retrain_status` — Прогресс авто-ретрейна
- `/tf` — Текущий таймфрейм и настройки
- `/help` — Справка

## Config Quick Reference
```yaml
# TF switching
tf_presets.active_preset: "1m"  # "5m" or "15m"

# Whitelist
market.whitelist_only: false    # true = only whitelist coins

# Partial TP
partial_tp.close_fraction: 0.3  # 30% close at TP1

# RL
rl.enabled: false               # Enable RL position manager
```

## Critical Rules
- MANUAL TRADES ARE SACRED
- FEE AWARENESS (0.06% taker fees)
- LANGUAGE: Ответы ТОЛЬКО на русском

## Backlog
Все P0/P1/P2 задачи выполнены. Следующие потенциальные улучшения:
- Бэктестирование с новыми параметрами
- Grade-based position sizing (A=100%, B=75%, C=50%)
- Dashboard/UI для мониторинга
- Multi-exchange support
