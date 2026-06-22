---
name: prd-bot-winning-entry-rules
description: >-
  Анализ всех сигналов с исходом TP (включая НЕ открытые на бирже): индикаторы,
  фильтры, стакан, зоны → правила удачных входов. Только чтение данных PRD-BOT.
---

# Правила удачных входов (TP) — Hermes

## Задача

Найти **общие черты** сигналов, которые **закрылись в плюс по TP**:
- сделки, реально открытые на Bybit;
- **пропущенные** сигналы, по симуляции skipped-backtest дошли бы до TP.

Цель: **систематизировать** параметры (RSI, ATR, imbalance, RR, regime, причина skip) и выдать **правила входа** — не менять config самому.

## Команда (на VPS)

```bash
cd /root/AGENT-WORLD   # или /root/PRD-BOT-ALL
./venv/bin/python3 scripts/hermes_winning_entry_rules.py --hours 336
```

Telegram-формат (HTML):

```bash
./venv/bin/python3 scripts/hermes_winning_entry_rules.py --hours 168 --telegram
```

## Файлы результата

| Файл | Содержание |
|------|------------|
| `data/learning/winning_entry_rules.json` | JSON: правила, медианы, счётчики |
| `data/learning/winning_entry_rules_report.md` | Отчёт для человека |

## Источники данных (только чтение)

| Путь | Роль |
|------|------|
| `data/supervisor/skipped_backtest/results.jsonl` | Виртуальный TP/SL пропущенных |
| `data/ledger/signal_ledger.jsonl` | Причина skip, confidence, raw |
| `data/trades/trade_history.jsonl` | Реальные TP + `entry_context` |

Supervisor должен периодически гонять skipped-backtest (`supervisor_v4.skipped_signal_backtest`).

## Формат ответа пользователю (русский)

1. **Сколько TP** — виртуальных vs реальных за период.
2. **Топ-3 правила** — поле, порог, % поддержки среди TP.
3. **Ложные пропуски** — какие `skip_reason` чаще всего у «до TP», но не вошли.
4. **Одна рекомендация (ZeroOne)** — одна переменная config или фильтр для обсуждения, **без авто-правки**.

## Связь с reflection loop

После отчёта запиши гипотезу в `~/.hermes/state/history/YYYY-MM-DD-winning-rules.md`:
- какое правило проверить на AGENT-WORLD;
- ожидаемый эффект (больше входов в TP / меньше ложных skip).

## Запрещено

- Менять `config.yaml` PRD-BOT без явной просьбы пользователя.
- Ордера на Bybit.
- Отключать фильтры «чтобы было больше сделок».

## Минимум данных

Нужно **≥3** исхода `take_profit` в `results.jsonl` или реальных TP в журнале. Если мало — скажи честно и предложи подождать 24–48 ч soak AGENT-WORLD.
