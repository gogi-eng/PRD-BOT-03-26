# Active Context

**Дата фокуса:** 20.08.2026 (UTC+3)
**Песочница:** active, hash `d7ead0b` (ветка на сервере ещё `02.08.26-AGENT-WORLD`).
**Прод:** inactive/masked — **не unmask**.

## Важно

**Не менять код/config бота без явного «да / делай / одобряю».**
Прод **не** unmask/start без явного «да».
Код стратегии лонгов готов локально — **push/деплой ждать «да»**.

## Фокус 20.08 — стратегия лонгов (Buy)

- Анализ: skipped_backtest 6974 Buy → WR 45.4%; блок часов 3/4/5/10/20 → WR 50.3% (отсечено 17.9%).
- Модуль: `prd_agent/entry/long_quality_gate.py`
- Выход Buy: `positions.long_swing_exit` (SL min 1%, trail 3.5/4.0, time-stop 240)
- Soft hours: Buy ≠ Sell; htf `1`/`-1` = aligned
- AW config ON / prod OFF
- Тесты: `test_long_quality_gate.py` — 15 passed (с hermes_briefing)
- Canvas: `long-trades-strategy.canvas.tsx`

## Маркеры логов

| Что | Маркер |
|-----|--------|
| Long quality | Long quality gate |
| Long SL widen | Long swing SL widen |
| Trailing GARCH | Trailing GARCH |
| Zone corridor | Zone corridor |
| SPIKE bypass | SPIKE bypass no_corridor |
| SPIKE pullback | SPIKE pullback: |
| CloseWatchdog | CloseWatchdog / АВАРИЯ ЗАКРЫТИЙ |

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| User | root |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |
