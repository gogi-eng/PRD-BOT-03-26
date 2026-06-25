# AI-рекомендации vs PRD-BOT — 24.06.2026

> Сверка отчёта «Обучение AI и рекомендации» с реальным состоянием бота.  
> **Правило:** не усиливать фильтры без статистики; ZeroOne — одна правка config за цикл.

## Уже есть (не дублировать)

| Область | В PRD-BOT |
|---------|-----------|
| Динамический риск | `risk_pct_per_trade`, пресеты, `dynamic_leverage` |
| ATR SL/TP | зоны S/R, trailing, пакет 1+2 |
| Лимиты просадки | daily loss, consecutive, cooldown, max_trades |
| Confluence | `quality_gate` 0.92, `entry_pipeline` |
| MTF / режим | `adaptive_regime_presets`, HTF 4h |
| Funding / OI | `funding_filter`, `derivatives_entry_guard` |
| ML | transformer blend, `rule_weight_learning`, `feedback_loop` |
| Все сигналы | `signal_ledger.jsonl` + virtual + skipped_backtest |

## Рационально позже

| Задача | Когда |
|--------|-------|
| `min_samples: 20` rule weights | 30.06 |
| `orderbook_entry` на PRD | 30.06 по цифрам |
| Leverage cap 10–15x | 30.06 / 03.07 |
| CI + тесты guard | 30.06 |
| On-chain API (Glassnode…) | не раньше июля, нужен бюджет |

## Не делать без решения

- Ослабление quality_gate / Speed Mode
- Снятие max_positions / daily loss
- Микросервисы, Redis, Postgres
- Новый LSTM «поверх» transformer без данных

## Карты сигналов для Hermes

Файлы в [Analise_Hermes](https://github.com/gogi-eng/Analise_Hermes):
- `hermes_signal_maps.jsonl` — карта на каждый сигнал
- `HERMES_SIGNAL_MAPS.md` — сводка

Обновление: каждые 3 ч (`hermes-signal-maps.timer` на сервере).
