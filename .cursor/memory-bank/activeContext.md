# Active Context

**Дата фокуса:** 15.08.2026 (UTC+3)
**Ветки дня:** 15.08.26-PRD-BOT-ALL · 15.08.26-AGENT-WORLD (см. hash после push)
**Проверка:** песочница active; прод masked/inactive — **не unmask**.

## Важно (правило пользователя)

**Не менять код/config бота без явного «да / делай / одобряю».**
Прод **не** unmask/start без явного «да».

## Текущий фокус (15.08 — Trailing GARCH)

1. `positions.trailing_volatility_regime` — GARCH calm/normal/storm → множитель дистанции трейлинг-SL.
2. AW: `enabled: true` (calm×0.75, normal×1.0, storm×1.35); прод: `enabled: false`.
3. Маркер лога: `Trailing GARCH` (при смене режима).
4. Проводка: `position_steward` (manage loop); startup в orchestrator. Signal agent не ведёт trailing SL.
5. Деплой: только AGENT-WORLD.

## Маркеры логов

| Что | Маркер |
|-----|--------|
| Trailing GARCH | Trailing GARCH |
| Manual time-stop skip | MANUAL SAFE skip time-stop |
| Companion skip manual | Companion skip manage / Companion skip close |
| Zone corridor | Zone corridor |
| SPIKE bypass | SPIKE bypass no_corridor |
| SPIKE pullback | SPIKE pullback: |
| SL/TP guard | Missing SL/TP on position, SL/TP guard |
| Manual SL guard | Manual SL missing, Manual SL guard |
| CloseWatchdog | CloseWatchdog / АВАРИЯ ЗАКРЫТИЙ |
| Volatility sizing | Volatility regime |

**Убрано откатом (не возвращать):** MANUAL SAFE skip SL/TP manage (флаг manage_sl_tp_manual).

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
