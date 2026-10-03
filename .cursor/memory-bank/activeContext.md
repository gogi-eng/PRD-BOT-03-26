# Active Context

**Дата фокуса:** 03.10.2026 (UTC+3)

## Разбор логов AW 27.09–03.10 и фикс 03.10

Снимок: песочница, 37 закрытий, **−28.93 USDT**. Худшие: BTW, US, ENA. Плюс держит MOVR.

Фикс (пользователь сказал «да», пуш в ветки дня, **деплой не делал**):

1. `BybitClient.get_orderbook` / `get_recent_trades` принимают `lazy` — иначе проверка стакана перед погоней за SPIKE падала и молчала (669 раз в логе).
2. Час из `preferred_utc_hours` обучение супервизора больше не закрывает снова. На песочнице это **12 и 13**. Часы **16–18** остаются: это блок открытия Нью-Йорка, не обучение. Чёрный список ETH/SOL/SOXL не трогал.
3. Тесты: `test_supervisor_v4.py` + `test_bybit_orderbook_lazy_kwargs.py` — 8 passed. Бэктест N/A (не SL/TP).

SSH на 207.154.238.178 — timeout. Деплой AW — только после команды пользователя.

## План 2 недели + закладки (27.09.2026)

- **Закладки агента:** `.cursor/memory-bank/researchBookmarks.md` (Bybit MCP, awesome lists, TimesFM 3.0, FreqAI RL, Forven).
- **План AW-only 27.09–11.10:** `progress.md` — MCP read-only + 3 эксперимента (TimesFM soak, multi-symbol lab, skipped lab vs TimesFM). **Prod не трогаем.**

## Сегодня: TimesFM gate (калибровка + фильтр по символу)

1. **`timesfm_gate`**: 5 сигналов по символу → если TimesFM угадал направление ≥80% → фильтр входа только для этого символа.
2. Пути: orchestrator + SPIKE/MARKET SCANNER (`telegram_signal_agent._try_execute_market_setup`).
3. Кнопка Telegram: **📈 TimesFM: ВКЛ/ВЫКЛ** (`act:toggle_timesfm`).
4. Config: AW sandbox `timesfm_gate.enabled: true`, прод `false`. State: `data/timesfm/gate_state.json`.
5. Тесты: `test_timesfm_gate.py` + `test_timesfm_bybit_compare.py` — **18 passed**. Бэктест N/A (фильтр ML, не SL/TP).

## ЗАДАЧА АГЕНТА (запрос пользователя 22.09.2026)

**Каждое воскресенье (UTC+3), если TimesFM включён** (`timesfm_gate.enabled: true` и/или глобально ВКЛ в Telegram / `gate_state.json`) — **сделать полный прогноз/разбор работы TimesFM** без ожидания напоминания.

Чеклист воскресного отчёта:
1. `data/timesfm/gate_state.json` — по символам: калибровка, accuracy, `trading_enabled`.
2. Логи AW: `journalctl -u trading_bot_agent_world` + `telegram_signal_agent_world` — `TimesFM calib|resolved|TRADING ON|block|pass`.
3. Лаб-скрипт по топ-символам: `bash scripts/run_timesfm_experiment_server.sh SYMBOL 50` (BTC, ETH, SOL + активные из логов).
4. Сводка: где фильтр помог бы / где мешал; рекомендация — оставить ВКЛ, ослабить порог, или ВЫКЛ по символу.
5. Краткий отчёт пользователю на русском; **код/config не менять без «да»**.

Первый контроль: **воскресенье 28.09.2026** (после деплоя gate `f16cac9`).

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
