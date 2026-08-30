# Active Context

**Дата фокуса:** 30.08.2026 (UTC+3)
**Прод:** **active** · ветка дня **`30.08.26-PRD-BOT-ALL`** (если не трогали — tip `29.08`) · GARCH + Trailing GARCH ON
**Песочница:** **active** · ветка **`30.08.26-AGENT-WORLD`** · **SPIKE/профиль входов откат к 22.08**

## Откат AW к профилю 22.08 (одобрено 30.08)

Анализ Bybit Performance + `trade_history`: плюс 29.06–04.07 и 24–25.08 был у **AW**, не у прода. Ужесточение 26.08 резало прибыльное окно 24–25.08.

| Параметр | Было (26.08 tighten) | Стало (как 22.08) |
|----------|----------------------|-------------------|
| max_positions | 6 | **8** |
| min_signal_confidence / QG | 0.87 | **0.85** |
| SPIKE execute_min_score | 76 | **72** |
| SPIKE min_move_pct | 4.5 | **4.0** |
| SPIKE min_volume_ratio | 1.55 | **1.40** |
| SPIKE extra_position_slots | 1 | **2** |
| market_scanner_execute_min_score | 78 | **75** |

**Не трогали:** notional 45%, плечо 10–15, Zone/GARCH/Long Quality/manual_sl_guard, TG каналы A+B, soft weight_overrides 0.45.

## Важно

- Прод **не** откатывать к июню (плечо 20–50×).
- Ручные сделки августа (+STORJ/XPL) ≠ параметры бота.

## Маркеры

| Что | Маркер |
|-----|--------|
| SPIKE | `SPIKE` / execute_min_score 72 |
| GARCH | `Volatility regime` / `Trailing GARCH` |
| Long Quality | long_quality_gate |

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |
