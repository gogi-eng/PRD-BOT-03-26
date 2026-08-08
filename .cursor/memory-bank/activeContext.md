# Active Context

**Дата фокуса:** 08.08.2026 (UTC+3)
**Ветки дня:** `08.08.26-AGENT-WORLD` · `08.08.26-PRD-BOT-ALL`

## Текущий фокус

1. **HOTFIX SNDKUSDT (08.08):** ручная Long на AW закрыта time-stop age=169m сразу после Adopted (stale opened_at от прошлой bot-сделки).
   - Фикс: `manual_auto_close: false` + Companion `auto_close_manual: false`; adopt manual → opened_at=now; не inherit `_bot_levels`.
   - 14:07 orderflow/trade_ok=False — отдельное событие (отказ входа + дневной лимит), не закрытие.
2. **HOTFIX BLESS:** Companion require_prior_trend / min_hold 300s
3. **08.08 P0+P1:** spike_bypass_no_corridor + pullback_entry (AW)
4. Wallet / SPIKE / polling / фильтры — **не отключать**

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |

## Не смешивать

- /root/PRD-BOT-ALL ← только *-PRD-BOT-ALL
- /root/AGENT-WORLD ← только *-AGENT-WORLD
