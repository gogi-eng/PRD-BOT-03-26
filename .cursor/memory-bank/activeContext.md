# Active Context

**Дата фокуса:** 08.08.2026 (UTC+3)
**Ветки дня:** `08.08.26-AGENT-WORLD` · `08.08.26-PRD-BOT-ALL`

## Текущий фокус

1. **08.08 P0+P1 на песочнице (одобрено):**
   - P0: `spike_bypass_no_corridor` — сильный SPIKE при `no_corridor` проходит (AW ON; prod OFF)
   - P1: SPIKE `pullback_entry` gate уже в коде; AW `enabled: true`, prod `enabled: false`
   - Маркер лога P0: `SPIKE bypass no_corridor`
   - Маркер лога P1: spike pullback WAIT/ENTER из `spike_pullback_gate`
2. Ранее: trailing after BE -0.5%; AW→prod knobs 06.08
3. Wallet / SPIKE / polling / фильтры — **не отключать**

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
