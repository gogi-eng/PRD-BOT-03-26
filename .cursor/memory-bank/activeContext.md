# Active Context

**Дата фокуса:** 20.09.2026 (UTC+3)

## Сегодня (одобрено): отчёты AW — CSV неделя + сводка «По дням»

1. **📥 CSV неделя** — кнопка Telegram рядом с «По дням»; файл из `trade_history.jsonl` за 7 дней → `data/exports/` + sendDocument.
2. **📅 По дням** — блок «Сводка периода»: лучший/худший день + серия убыточных дней подряд (streak).
3. Тесты: `backend/tests/test_daily_pnl_and_lab_reports.py` — **8 passed**; бэктест N/A (отчётность).
4. Ветки: `20.09.26-AGENT-WORLD` + cherry-pick `20.09.26-PRD-BOT-ALL`.
5. Деплой: **только AGENT-WORLD** (прод не трогать без отдельного «да»).

## Жёсткое правило агента (16.09.2026)

**Тестировать все предлагаемые изменения** — до «готово»/push: `py_compile` + pytest + бэктест + отчёт в чат.
Файл: `.cursor/rules/test-backtest-before-deploy.mdc` (alwaysApply).

## Предыдущий фокус (15–19.09)

- SL/TP guard SELL, manual_sl_guard prod, SPIKE lock, часы 16–18 МСК.
- GARCH на проде (config flags).
- Суточные отчёты 18–19.09: прод ≈ +0,11; AW ≈ +6,57 (хрупкий плюс AKE).

## ОТКАТ

| Инстанс | Ветка / тег |
|---------|-------------|
| Прод | `06.08.26-PRD-BOT-ALL` / тег `rollback-pre-A-B-2026-08-26-prod` |
| Песочница | `02.08.26-AGENT-WORLD` / тег `rollback-pre-A-B-2026-08-26-aw` |

Не удалять rollback-ветки/теги.

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |
