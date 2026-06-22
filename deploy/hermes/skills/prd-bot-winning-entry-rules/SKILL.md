---
name: prd-bot-winning-entry-rules
description: >-
  Анализ всех сигналов с исходом TP (включая НЕ открытые на бирже): индикаторы,
  фильтры, стакан, зоны → правила удачных входов. Только чтение данных PRD-BOT.
---

# Правила удачных входов (TP) — Hermes

## Задача

Найти **общие черты** сигналов и сделок по исходам:

| Исход | Описание |
|-------|----------|
| **Профит** | Закрытие в плюс / виртуальный TP |
| **Убыток** | SL / минус |
| **Безубыток** | Минимальный ±0 (нейтраль) |

Источники:
- реальные сделки на Bybit (`trade_history.jsonl` + `entry_context`);
- **пропущенные** сигналы (симуляция skipped-backtest).

Цель:
1. **Правила удачных входов** (TP).
2. **Влияние фильтров и индикаторов** на качество — усилить вес (`increase_weight`) или **отказаться** от фильтра (`consider_remove`).

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
4. **Одна рекомендация (ZeroOne)** — одна переменная config или один вес soft-rule.

## Рекомендации по действиям

| action | Значение |
|--------|----------|
| `increase_weight` | Правило часто при профите — усилить в `rule_weight_learning` |
| `decrease_weight` | Слабое правило — снизить влияние |
| `consider_remove` | Фильтр отсекает много виртуальных TP — ослабить или убрать |
| `keep` | Фильтр спасает от SL — не трогать |

Файл `suggested_rule_weights` в JSON — **предложение**, не авто-применение.

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
