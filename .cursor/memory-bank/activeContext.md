# Active Context

**Дата фокуса:** 06.08.2026 (UTC+3)  
**Ветки дня:** `06.08.26-AGENT-WORLD` · `06.08.26-PRD-BOT-ALL`  
**База:** tip `04.08.26-*`

## Текущий фокус

1. **Прод: BE+ / трейлинг не в минус при откате**
   - `be_lock_extra_pct: 1.0` (было 0.70)
   - `trailing_after_be` ON, `widen_mult: 1.25`
   - Код: пол trailing SL = fee + lock (не только fee)
2. Daily-loss reset / Order OK qty — уже в 04.08
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
