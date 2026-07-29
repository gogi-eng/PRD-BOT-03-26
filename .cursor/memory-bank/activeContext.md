# Active Context

**Дата фокуса:** 29.07.2026 (UTC+3)  
**Ветки дня (для push):** `29.07.26-AGENT-WORLD` · `29.07.26-PRD-BOT-ALL`  
**Hashes:** AW `af80fa4` · PRD `6977db7`

## Текущий фокус

1. **Trailing after BE** — после переноса SL в BE/BE+ чуть шире trailing distance (`widen_mult: 1.2`)
   - Код: `prd_agent/positions/trailing_after_be.py` + wiring в `position_steward.py`
   - Config: AW `enabled: true` / prod `enabled: false`
   - Лог-маркер: `Trailing after BE widen`
   - Деплой: сначала AGENT-WORLD, прод после soak
2. Wallet Tracker / liquid pairs / SPIKE / polling — **не отключать**

## Не смешивать

- `/root/PRD-BOT-ALL` ← только `*-PRD-BOT-ALL`
- `/root/AGENT-WORLD` ← только `*-AGENT-WORLD`
