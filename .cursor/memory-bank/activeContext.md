# Active Context

**Дата фокуса:** 09.08.2026 (UTC+3)
**Ветки дня:** `09.08.26-AGENT-WORLD` · `09.08.26-PRD-BOT-ALL`

## Текущий фокус

1. **SL/TP guard (09.08):** периодическая проверка открытых позиций — на бирже должны быть stopLoss и takeProfit; иначе лог + восстановление через `update_stop_loss` / `update_take_profit`.
   - Модуль: `prd_agent/positions/sl_tp_guard.py`, вызов в `PositionSteward.manage()`
   - Config: `positions.sl_tp_guard` (оба deploy yaml), `include_manual: true` (защита капитала ≠ Companion auto-close)
   - Маркеры лога: `Missing SL/TP on position`, `SL/TP guard`
2. **HOTFIX SNDKUSDT (08.08):** `manual_auto_close: false` + Companion `auto_close_manual: false` — не откатывать
3. Wallet / SPIKE / polling / фильтры / bybit_monitor — **не отключать**

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| User | root |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |

## Не смешивать

- /root/PRD-BOT-ALL ← только *-PRD-BOT-ALL
- /root/AGENT-WORLD ← только *-AGENT-WORLD
