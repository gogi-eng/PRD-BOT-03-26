# Чеклист: что трогаем только в `prd_agent/`

Ветка **ALGO** (`12.06.26-ALGO`) — алгоритмика и архитектура. Прод не ломаем.

## Продакшен (не трогать в ALGO без merge в OPT-ALL)

| Путь | Роль |
|------|------|
| `run_unified.py` | Точка входа на сервере |
| `deploy/config.production.yaml` | Копируется на сервер через install-скрипт |
| `deploy/*.service` | systemd |

## Разрешено менять в ALGO

| Путь | Этап |
|------|------|
| `prd_agent/exchange/api_cache.py` | 1 — TTL, orderbook/trades |
| `prd_agent/exchange/api_stats.py` | 1 — счётчик API |
| `prd_agent/exchange/bybit_adapter.py` | 1 — tickers/cycle, lazy fetch |
| `prd_agent/entry/retest_watchlist.py` | 2 — ретест как состояние |
| `prd_agent/entry/entry_pipeline.py` | 3 — scoring вместо бинарных reject |
| `prd_agent/strategies/*` | 4 — Scalp / Swing |
| `prd_agent/engine/orchestrator.py` | 1–4 — интеграция |
| `prd_agent/risk/quality_gate.py` | 1 — tickers_map |
| `prd_agent/reporting/bi_hourly.py` | 1 — блок API-нагрузки |
| `prd_agent/telemetry/skip_baseline.py` | 0 — baseline SKIP |
| `prd_agent/analysis/signal_ledger.py` | 0 — skip_by_reason |
| `backend/tests/test_*.py` | 0–4 — тесты |
| `docs/ROADMAP_ALGO_*.md` | 0 — план |

## Не трогаем в ALGO

| Путь | Почему |
|------|--------|
| `bot/` + mixins | Legacy, не на прод-сервере |
| `engine/entry_engine.py` | Общая библиотека; менять только при необходимости моста |
| `exchange/bybit_client.py` | Низкий уровень; кеш в `prd_agent` |
| Сервер / systemd | Только ветка `ДД.ММ.ГГ-OPT-ALL` |

## Ветки

- **`ДД.ММ.ГГ-OPT-ALL`** — ежедневный деплой, hotfix, Telegram, sync.
- **`ДД.ММ.ГГ-ALGO`** — алгоритмика; в прод после тестов и merge.

## Критерии готовности этапов

| Этап | Критерий |
|------|----------|
| 0 | Baseline % SKIP по причинам за 7 дней в отчёте |
| 1 | 24 ч без `circuit open` / rate limit в логах |
| 2 | Лог `retest_watch: SYM WAIT → CONFIRMED` |
| 3 | SKIP quality_gate/pullback 40–60% (virtual trades) |
| 4 | Разные профили SKIP по scalp vs swing в отчёте |
