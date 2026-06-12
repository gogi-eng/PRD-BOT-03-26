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

## Этап 1 (следующий код в ALGO)

- [ ] `api_cache`: кеш tickers на весь цикл
- [ ] Lazy orderbook/trades после дешёвых фильтров
- [ ] Счётчик `api_calls_per_cycle` в лог

## Этап 2

- [ ] `retest_watchlist.py` + тесты
- [ ] Интеграция в `entry_engine_bridge.py`

## Этап 3

- [ ] `entry_pipeline.py` scoring mode
- [ ] Пресеты strict/balanced/aggressive
