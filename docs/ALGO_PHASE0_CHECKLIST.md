# ALGO Phase 0 — чеклист перед кодом

Ветка: `12.06.26-ALGO`

## Правило

- Продакшен: `run_unified.py` + `prd_agent/`
- Legacy `bot/` — не трогать без явной необходимости
- Деплой сервера — только из `ДД.ММ.ГГ-OPT-ALL`

## Baseline (собрать на сервере)

```bash
# Последние 24ч — топ причин SKIP
grep -E "Skip |SKIPPED|quality_gate|pullback|impulse_retest|zone_entry" /root/PRD-BOT-ALL/bot.log | tail -200

# API circuit / rate limit
grep -iE "circuit|rate.?limit|429" /root/PRD-BOT-ALL/bot.log | tail -50
```

## Этап 1

- [x] `api_cache`: orderbook/trades TTL + tickers на цикл
- [x] Lazy orderbook/trades (`signal_passed_cheap_filters`)
- [x] `ApiCallJournal` + лог `API cycle N: X REST calls`
- [x] Блок API в bi-hourly отчёте

## Этап 2

- [x] `retest_watchlist.py` + тесты
- [x] Интеграция в `orchestrator.py` (WAIT → CONFIRMED)

## Этап 3

- [x] `entry_pipeline.py` scoring (strict/balanced/aggressive)
- [x] Пресеты 🛡/⚖️/🚀 → entry_pipeline.mode

## Этап 4

- [x] `strategies/scalp_strategy.py`, `swing_strategy.py`, `router.py`
- [x] `trading.active_strategy` + scalp_hours_utc

## Baseline CLI

```bash
python scripts/algo_skip_baseline.py --hours 168
```
