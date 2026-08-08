# Active Context

**Дата фокуса:** 08.08.2026 (UTC+3)
**Ветки дня:** `08.08.26-AGENT-WORLD` · `08.08.26-PRD-BOT-ALL`

## Текущий фокус

1. **HOTFIX BLESS:** Companion ложный «разворот» SMA закрыл Long на откате (прод −$0.43).
   - Фикс: `require_prior_trend` (flip), `min_hold_sec=300`, max_loss −1.5→−3.5, min_profit 0.3→0.8
2. **08.08 P0+P1 на песочнице:** spike_bypass_no_corridor (AW ON); pullback_entry (AW ON / prod OFF)
3. Ранее: trailing after BE −0.5%; AW→prod knobs 06.08
4. Wallet / SPIKE / polling / фильтры — **не отключать**

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
