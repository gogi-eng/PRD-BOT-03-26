# Active Context

**Дата фокуса:** 15.08.2026 (UTC+3)
**Ветки дня:** 15.08.26-PRD-BOT-ALL · 15.08.26-AGENT-WORLD (после push)
**Проверка:** песочница active; прод masked/inactive — **не unmask**.

## Важно (правило пользователя)

**Не менять код/config бота без явного «да / делай / одобряю».**
Прод **не** unmask/start без явного «да».

## Текущий фокус (15.08 — сделано по «Делаем 8, 9, 10»)

1. **CloseWatchdog age≈0:** ненадёжный возраст (adopt/снимок) ≠ fast-loss; копейки не копят streak; метка «мгновенный учёт».
2. **manual_sl_guard:** AW ON / prod OFF — ставит защитный SL на manual без стопа; trailing/BE+ **не** отключает (≠ откат manage_sl_tp_manual).
3. **Отчёт 20:00:** generate_daily_report.py --fetch-ssh; ярлык → report_2026-08-15.md с PnL; задача Windows с --fetch-ssh.

## Маркеры логов

| Что | Маркер |
|-----|--------|
| Manual time-stop skip | MANUAL SAFE skip time-stop |
| Companion skip manual | Companion skip manage / Companion skip close |
| Zone corridor | Zone corridor |
| SPIKE bypass | SPIKE bypass no_corridor |
| SPIKE pullback | SPIKE pullback: |
| SL/TP guard | Missing SL/TP on position, SL/TP guard |
| Manual SL guard | Manual SL missing, Manual SL guard |
| CloseWatchdog | CloseWatchdog / АВАРИЯ ЗАКРЫТИЙ |

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
