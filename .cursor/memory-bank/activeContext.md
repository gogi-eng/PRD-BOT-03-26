# Active Context

**Дата фокуса:** 06.08.2026 (UTC+3)  
**Ветки дня:** `06.08.26-AGENT-WORLD` · `06.08.26-PRD-BOT-ALL`

## Текущий фокус

1. **Оба бота: после BE трейлинг уже на 0.5 п.п.**
   - `trailing_after_be.enabled: true`
   - `distance_reduce_pct: 0.5` (было `widen_mult: 1.25` — расширяло; заменено)
   - Маркер лога: `Trailing tighten after BE −0.5%`
   - Пол trailing SL = fee + lock (BE+) — без изменений
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
