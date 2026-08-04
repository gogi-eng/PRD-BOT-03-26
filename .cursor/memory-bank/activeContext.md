# Active Context

**Дата фокуса:** 04.08.2026 (UTC+3)  
**Ветки дня:** `04.08.26-PRD-BOT-ALL` / `04.08.26-AGENT-WORLD`

## Текущий фокус

1. **Сделано 04.08 (одобрено «делай 1, 3»):**
   - Daily-loss manual reset в git: флаг `data/risk_daily_loss_manual_reset.json` до конца торгового дня (timezone_offset), reconcile не перетирает PnL; маркер `MANUAL_DAILY_LOSS_RESET`.
   - Order OK: в лог передаётся `qty` (убран TypeError).
2. Пункт 2 (XRP blacklist) — **не делали**. Trailing/фильтры/auto_apply prod — не трогали.
3. Live: risk 0.225, Zone/GARCH ON, auto_apply prod **false** / AW **true**.

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

