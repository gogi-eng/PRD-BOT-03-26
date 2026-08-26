# Active Context

**Дата фокуса:** 26.08.2026 (UTC+3)
**Прод:** **active** · ветка **`26.08.26-PRD-BOT-ALL`** · GARCH sizing + Trailing GARCH **ON**
**Песочница:** **active** · ветка **`26.08.26-AGENT-WORLD`** · фильтры **чуть уже** (меньше входов)

## Фильтры AW (одобрено 26.08 вечер)

Цель: меньше сделок на песочнице, не убивая торговлю. Только `deploy/config.agent_world_sandbox.yaml`.

| Параметр | Было | Стало |
|----------|------|-------|
| max_positions | 8 | 6 |
| min_signal_confidence / QG | 0.85 | 0.87 |
| SPIKE execute_min_score | 72 | 76 |
| SPIKE min_move_pct | 4.0 | 4.5 |
| SPIKE min_volume_ratio | 1.40 | 1.55 |
| SPIKE extra_position_slots | 2 | 1 |
| market_scanner_execute_min_score | 75 | 78 |
| TG parser / AI_exec | 82(def)/55(def) | 85 / 60 |
| min_openrouter_confidence | 68 | 72 |
| min_structure_score | 58 | 62 |
| soft weight_overrides | 0.55 | 0.45 |

**Не трогали:** polling, панель, каналы A+B, Zone/HTF/GARCH ON, hedge OFF (one-way).

## Важно (A+B 26.08) — на месте

- **A)** Дневные ветки 26.08 на оба инстанса.
- **B)** На AGENT-WORLD чтение TG-каналов — **не откатывали**.
- One-way: `force_one_way_mode` / `positionIdx=0`.

## ОТКАТ

| Инстанс | Ветка / тег |
|---------|-------------|
| Прод | `06.08.26-PRD-BOT-ALL` / тег `rollback-pre-A-B-2026-08-26-prod` |
| Песочница | `02.08.26-AGENT-WORLD` / тег `rollback-pre-A-B-2026-08-26-aw` |

Не удалять rollback-ветки/теги.

## Маркеры

| Что | Маркер |
|-----|--------|
| GARCH sizing | `Volatility regime` |
| Trailing GARCH | `Trailing GARCH` |
| One-way | `Bybit force_one_way_mode: ok=True mode=one_way` |
| AW каналы | `Got difference for channel` / `TG_AGENT] started` |
| SPIKE score | execute_min_score / SPIKE pullback |

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |
