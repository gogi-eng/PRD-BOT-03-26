# Active Context

**Дата фокуса:** 22.09.2026 (UTC+3)

## Сегодня: TimesFM gate (калибровка + фильтр по символу)

1. **`timesfm_gate`**: 5 сигналов по символу → если TimesFM угадал направление ≥80% → фильтр входа только для этого символа.
2. Пути: orchestrator + SPIKE/MARKET SCANNER (`telegram_signal_agent._try_execute_market_setup`).
3. Кнопка Telegram: **📈 TimesFM: ВКЛ/ВЫКЛ** (`act:toggle_timesfm`).
4. Config: AW sandbox `timesfm_gate.enabled: true`, прод `false`. State: `data/timesfm/gate_state.json`.
5. Тесты: `test_timesfm_gate.py` + `test_timesfm_bybit_compare.py` — **18 passed**. Бэктест N/A (фильтр ML, не SL/TP).

## Ранее: кнопка «📐 GARCH правила» в Telegram

1. **Цепочка кода цела** (локально / origin `20.09.26-*`):
   `act:garch_rules` → `control_bot` → `orch.get_manual_trailing_garch_report()` →
   `steward.get_manual_trailing_garch_summary()` → `learner.telegram_rules_summary()`.
2. **Это отчёт, не переключатель ВКЛ/ВЫКЛ.** Включение обучения — в config
   `manual_trailing_garch_learning.enabled` (AW sandbox: `true`). Прод deploy yaml
   блока learning **нет** (только sizing + Trailing GARCH).
3. Уточнён текст отчёта: явно «ВКЛ/ВЫКЛ» + «это отчёт». Тесты: `test_manual_trailing_garch_learner` + peak retrace — **9 passed**.
4. Push: AW **`748384f`**, prod **`cf9b69a`**. **SSH к 207.154.238.178 — timeout.** Деплой AW вручную на сервере.
5. CSV/PnL ранее: AW `7e831f5` · prod `253ec7a` — деплой AW мог не пройти. Целевой tip AW сейчас **`748384f`** (включает CSV + ясный текст GARCH).

## Жёсткое правило агента (16.09.2026)

**Тестировать все предлагаемые изменения** — до «готово»/push: `py_compile` + pytest + бэктест + отчёт в чат.
Файл: `.cursor/rules/test-backtest-before-deploy.mdc` (alwaysApply).

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
