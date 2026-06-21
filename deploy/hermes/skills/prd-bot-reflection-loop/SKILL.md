---
name: prd-bot-reflection-loop
description: Self-improving loop (ZeroOne): после N закрытых сделок PRD-BOT — reflect, одна гипотеза, без авто-правок config.
---

# Reflection loop PRD-BOT (метод ZeroOne)

## Когда использовать

- Пользователь просит «разбор сделок», «улучши стратегию», «reflection»
- Cron: каждые `reflection.every_n_closed_trades` закрытых сделок (см. `~/.hermes/strategy-goals.yaml`)
- После серии SL или слабого месяца

## Принцип (научный метод)

1. **Baseline** — метрики за последние 30d: PnL %, max DD, Sharpe (если данных мало — честно сказать).
2. **Reflect** — что сработало / что нет (skip reasons, часы UTC+3, RR, трейлинг).
3. **Hypothesis** — **ровно одна** переменная из `strategy-goals.yaml` → `allowed_variables`.
4. **Record** — записать гипотезу в `~/.hermes/state/history/YYYY-MM-DD-hypothesis.md` (не трогать config.yaml бота).

## Источники данных (только чтение)

- `strategy-goals.yaml` в `~/.hermes/`
- Ledger: `/root/PRD-BOT-ALL/data/ledger/`
- Skip: `/root/PRD-BOT-ALL/data/supervisor/skip_stats.json`
- Логи: `tail` bot.log прод и AGENT-WORLD

## Запрещено

- Менять `config.yaml` PRD-BOT без явной просьбы
- Ордера на Bybit
- Менять больше одной переменной за цикл

## Формат ответа (русский)

1. **Счёт** — сделки, win rate, PnL, DD (если есть)
2. **Главная проблема** — одна фраза
3. **Гипотеза** — одна переменная + ожидаемый эффект
4. **Действие пользователю** — «нажмите кнопку в Telegram» или «скажите — внесу в config»

После успешного цикла — сохрани улучшения grep/путей как skill (self-improvement Hermes).
