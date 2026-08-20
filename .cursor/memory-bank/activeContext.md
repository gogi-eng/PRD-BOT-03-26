# Active Context

**Дата фокуса:** 20.08.2026 (UTC+3)
**Песочница:** active · hash `df23ae3` · ветка `20.08.26-AGENT-WORLD`
**Прод:** **active** · hash `564be82` · ветка `20.08.26-PRD-BOT-ALL` · Long Quality Gate ON + panel Conflict fix

## Важно

Прод: панель `/panel` — Conflict больше **не** гасит polling.
Баланс при старте ~50 USDT (после фиксов ключей).

## Фокус

1. Long Quality Gate на проде (испытание).
2. Fix: `ControlBot.on_polling_error` — Conflict/сеть → панель жива.

## Маркеры

| Что | Маркер |
|-----|--------|
| Long quality | Long quality gate |
| Long SL widen | Long swing SL widen |
| TG Conflict | Telegram Conflict (панель НЕ останавливаем) |

## Ветки

| Ветка | Hash |
|-------|------|
| 20.08.26-PRD-BOT-ALL | 564be82 |
| 20.08.26-AGENT-WORLD | df23ae3 |

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |
