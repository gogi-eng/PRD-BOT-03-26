# Active Context

**Дата фокуса:** 26.08.2026 (UTC+3)
**Прод:** **active** · ветка **`26.08.26-PRD-BOT-ALL`** · GARCH sizing + Trailing GARCH **ON**
**Песочница:** **active** · hash **`61b2e19`** · ветка **`26.08.26-AGENT-WORLD`**

## GARCH на проде (одобрено 26.08)

- Пользователь: включить **нынешний** GARCH на проде, **без** модернизации 5–10x TF.
- Config prod: `volatility_regime_sizing.enabled: true`, `positions.trailing_volatility_regime.enabled: true` (как AW).
- Код не меняли — только флаги + тест assert prod ON.
- Маркеры: `Volatility regime`, `Trailing GARCH`.
- Откат: ветка `06.08.26-PRD-BOT-ALL`.

## Срочный фикс (вечер 26.08) — positionIdx / без хеджа

- Счёт AW → Merged Single; код `force_one_way_mode` / `positionIdx=0`.
- Лог-маркер: `Bybit force_one_way_mode: ok=True mode=one_way`.

## Важно (A+B 26.08) — на месте

- **A)** Дневные ветки 26.08 на оба инстанса.
- **B)** На AGENT-WORLD чтение TG-каналов — **не откатывали**.

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

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |
