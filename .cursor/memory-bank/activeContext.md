# Active Context

**Дата фокуса:** 02.08.2026 (UTC+3)  
**Ветки дня:** 02.08.26-PRD-BOT-ALL @ 2aa04e7 / 02.08.26-AGENT-WORLD @ f2d6d37

## Текущий фокус

1. **Внедрение оценки 02.08.26 (без ослабления фильтров):**
   - Прод: `self_improvement.auto_apply_low_risk: false` + rate-limit 1 правка/час в коде
   - PnL «📅 По дням»: бот / ручные / итог (`daily_pnl_split_origin: true`)
   - **Не трогали:** trailing, BE+, orderbook на прод, фильтры входа
2. **Наблюдение 2–3 дня (AGENT-WORLD) — руками пользователя:**
   - [ ] Кнопка **🧪 Лаборатория** — смотреть WR пропусков и топ SKIP-причин
   - [ ] Не ослаблять фильтры «на глаз» — только после данных Лаборатории
   - [ ] Не менять trailing / BE+ / orderbook на проде
   - [ ] Один рычаг потом (если Лаборатория покажет явный перекос) — не пачка правок сразу
3. Live config до деплоя (02.08): risk 0.225, leverage min 10, Zone ON, GARCH ON (оба), adopt_manual true, trailing_activation 2.5, **auto_apply было true на обоих** → после деплоя прод false.
4. SSH OK: IP 207.154.238.178.

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
