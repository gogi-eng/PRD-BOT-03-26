# Журнал решений

| Дата | Решение | Почему | Не откатывать |
|------|---------|--------|---------------|
| 19.07 | Hermes OFF | Вирт.TP → советы снять защиту | `hermes.enabled: false` |
| 19.07 | soft ×0.55 прод | Отрицательный lift soft-правил | weight_overrides |
| 19.07 | NY skip сб/вс/праздники | Ложный блок сессии акций | skip_weekends/holidays |
| 20.07 | pnl=0 ≠ серия | Безубыток раздувал panic | RiskGuard только pnl < 0 |
| 21.07 | Memory Bank в **git** PRD-BOT | Любой ПК после pull; аккаунт Cursor + project rules | `.cursor/memory-bank/` + alwaysApply |
| 21.07 | Не ставить полный RooFlow | Нужен Cursor, не Roo Code | только идея Memory Bank |

## Сессия 21.07 (запрос пользователя)

- Хочет: память **всегда** читалась под его ником Cursor на любом компе
- Хочет: текущая сессия **автоматом** в memory-bank  
→ канон в репозитории + alwaysApply rule + автообновление после значимых ходов
