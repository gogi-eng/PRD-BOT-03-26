# ROADMAP ALGO — PRD-BOT-ALL (12.06.26)

Ветка: **`12.06.26-ALGO`** (алгоритмика и архитектура, отдельно от ежедневной `ДД.ММ.ГГ-OPT-ALL`).

Источники анализа:
- `🏆 ОБЩАЯ ОЦЕНКА 12.06.26.txt` — глубокий разбор legacy `bot/` + mixins
- `chat-PRD-BOT Code Review (1).txt` — внешний code review (архитектура, ML, тесты)
- `BOT_DUMP.txt` — снимок `bot/` от 12.06.2026 (203 KB `main.py`, 16 mixins)
- Сверка с **продакшен-путём**: `run_unified.py` → `prd_agent/engine/orchestrator.py`
- Аналоги в индустрии: event-driven боты с WebSocket + локальный кэш (Bybit V5, snapshot+delta orderbook)

---

## 1. Сводное заключение (консенсус 3 отчётов + код)

### Общая оценка: **7.5–8/10 по идее**, **5–6/10 по исполняемости legacy-стека**

| Критерий | Оценка | Комментарий |
|----------|--------|-------------|
| Торговая логика (SMC, риск) | 8/10 | RiskGuard, basket lock, SMC/BOS — сильная база |
| Архитектура legacy `bot/` | 4/10 | God Object, тройной `__init__`, 16 mixins, 23 guard'а подряд |
| Архитектура **unified `prd_agent`** | 7/10 | Оркестратор, супервизор, кэш API — правильное направление |
| API / латентность | 5/10 | REST в цикле — главный риск бана Bybit (все отчёты сходятся) |
| Тестируемость | 6/10 | 100+ backend-тестов есть, но legacy почти без изоляции |
| ML-стек | 5/10 | Transformer + RL + Claude + Gemma — риск переобучения и «чёрных ящиков» |

### Главный парадокс (все отчёты)

Бот пытается быть **скальпером + свингом + SMC + ML + AI** одновременно.  
Результат: **«смерть от 1000 фильтров»** — сигналы гибнут на guard'ах, точки входа упускаются.

### Критичное уточнение для внедрения

**На сервере работает НЕ `bot/trading_bot.py`, а `prd_agent`.**  
`BOT_DUMP.txt` описывает **legacy** (всё ещё в репо для тестов/истории).  
Рефакторинг mixins — **низкий приоритет**, если unified стабилен.  
Все новшества ALGO внедряем в **`prd_agent/`**, legacy только синхронизируем по мере необходимости.

### Уже сделано в unified (не повторять в roadmap)

| Компонент | Где |
|-----------|-----|
| API-кеш price/klines/tickers | `prd_agent/exchange/api_cache.py` |
| Валидация config при старте | `prd_agent/config_validate.py` |
| Адаптивный интервал цикла | `prd_agent/engine/adaptive_loop.py` |
| Пресеты риска (Telegram) | `prd_agent/config_presets.py` |
| Supervisor V4 + virtual trades | `prd_agent/supervisor/supervisor_v4.py` |
| Вход по зонам/BOS/ретест | `prd_agent/entry/` + `engine/entry_engine.py` |
| Антиспам рассинхрона позиций | `prd_agent/positions/sync_guard.py` |
| Circuit pause на API | `prd_agent/exchange/bybit_adapter.py` |

---

## 2. Что подтверждают внешние архитектуры (сеть)

Типовая схема low-latency crypto bot (2024–2026):

```
WebSocket (tickers, kline, orderbook, trades)
        ↓
MarketDataCache (in-memory / Redis)
        ↓
Signal Engine (SMC, orderflow, стратегии)
        ↓
Risk Manager → Order Router → State Manager
```

Ключевые практики:
- **Snapshot + delta** для стакана (sequence gap → переснимок)
- **Observer / pub-sub** между данными и стратегиями
- Сканирование читает **кэш**, не REST в цикле
- Отдельные **режимы стратегии** (scalp vs swing), не один God pipeline

Это совпадает с рекомендациями из обоих code review.

---

## 3. Приоритеты внедрения (этапы)

### Этап 0 — ALGO baseline (эта ветка, 1–2 дня)

**Цель:** зафиксировать план, не ломая прод.

- [x] Документ roadmap (`docs/ROADMAP_ALGO_12.06.26.ru.md`)
- [ ] Чеклист «что трогаем только в prd_agent»
- [ ] Baseline-метрики: % SKIP по причинам за 7 дней (ledger + supervisor)

**Критерий готовности:** ветка `12.06.26-ALGO` на GitHub, команда знает разницу ALGO vs OPT-ALL.

---

### Этап 1 — P0: API и латентность (неделя 1–2)

**Проблема из отчётов:** 7–8 REST на символ × 15 монет ≈ риск rate limit.

**Действия (prd_agent):**

1. **Расширить `api_cache`**
   - TTL orderbook/trades (если ещё REST — реже, чем цикл)
   - Один `get_tickers()` на цикл → словарь для всех символов
2. **Lazy fetch**
   - Orderbook/trades только если сигнал прошёл дешёвые фильтры (HTF, ATR, час supervisor)
3. **Журнал API-нагрузки**
   - Счётчик запросов/цикл в лог + bi-hourly отчёт

**Не делаем сразу:** полный WebSocket (этап 4).

**Критерий:** 24 ч работы без `rate limit` / circuit open в логах.

---

### Этап 2 — P0: Ретест как состояние (неделя 2–3)

**Проблема:** impulse→retest проверяется только на **текущих 3 свечах**; 80% ретестов пропускаются (отчёт F).

**Действия:**

1. `prd_agent/entry/retest_watchlist.py`
   - Состояние: `{symbol: {side, bos_level, zone, expires_at, phase}}`
   - При BOS/пробое зоны → `WAIT_RETEST`
   - На каждом цикле: проверка ретеста, не только последние 3 свечи
2. Интеграция в `EntryEngineBridge` и orchestrator
3. Тесты: `test_retest_watchlist.py`

**Критерий:** в логах `retest_watch: BTCUSDT WAIT → CONFIRMED` без повторного BOS.

---

### Этап 3 — P1: Guard pipeline → scoring (неделя 3–5)

**Проблема:** 15–23 бинарных reject (оба review).

**Действия:**

1. `prd_agent/entry/entry_pipeline.py`
   - Фильтры с **весом**, не только reject
   - Порог composite (как EntryEngine v6), единый `reject_reason` + score
2. Режимы:
   - `strict` — как сейчас (много блокировок)
   - `balanced` — score ≥ 5 из 8
   - `aggressive` — score ≥ 4, размер позиции ×0.5
3. Связка с Telegram-пресетами (уже есть 🛡/⚖️/🚀)

**Критерий:** доля SKIP `quality_gate` / `pullback` снижается с >90% до 40–60% без роста просадки (virtual trades).

---

### Этап 4 — P1: Strategy Pattern — Scalp / Swing (неделя 5–7)

**Проблема:** один pipeline для разных таймфреймов.

**Действия:**

1. `prd_agent/strategies/scalp_strategy.py` — 1m/5m, без 4H, быстрый path
2. `prd_agent/strategies/swing_strategy.py` — 15m/4h, BOS, зоны, строже HTF
3. `StrategyRouter` выбирает по `trading.active_strategy` или часу (supervisor)
4. Убрать «фейковые» `has_bos=True` для scalp в legacy (если legacy ещё нужен)

**Критерий:** разные профили SKIP в отчёте по стратегиям.

---

### Этап 5 — P2: WebSocket market data (неделя 7–10)

**По отчётам — критично для HFT; для вашего депозита — важно для стабильности API.**

**Действия:**

1. `prd_agent/exchange/ws_feed.py` — Bybit V5 public WS
   - `kline.15`, `tickers`, опционально `orderbook.50`
2. `MarketDataStore` — in-memory, обновление из WS
3. Orchestrator читает klines/price из store; REST только bootstrap + gap recovery
4. Тест: 24 ч без REST storm

**Ссылки:** Bybit V5 WebSocket, паттерн snapshot+delta для стакана.

---

### Этап 6 — P2: ML упрощение + телеметрия (неделя 10–12)

**Консенсус review:** оставить **один** предиктор + feedback loop.

**Действия:**

1. Режим `ml.mode: off | transformer | shadow` — shadow только логирует, не блокирует
2. `prd_agent/telemetry/signal_metrics.py` — каждый сигнал: score, guards, исход, PnL
3. Bi-hourly: top-5 причин SKIP, winrate по source
4. Отключить mandatory AI gate → `fail_open` + timeout 5s (graceful degradation)

**Критерий:** при падении OpenRouter/Gemma бот торгует по TA+SMC.

---

### Этап 7 — P3: Корреляция и Relative Strength (месяц 3)

**Рекомендация G из оценки:** не только блокировать ETH из-за BTC, а выбирать сильнейшего.

1. `prd_agent/analysis/relative_strength.py`
2. При конфликте корреляции — ранжирование кандидатов по RS(4h)
3. Интеграция в `SignalRouter.merge_and_rank`

---

### Этап 8 — Legacy `bot/` (фоновый, низкий приоритет)

Только если снова понадобится `bot/trading_bot.py`:

- Удалить дубли `trading_bot_init_body.py` / aggregate `main.py` из runtime
- Единый `BotConfig` dataclass (частично есть в `core/config.py`)

**Продакшен не зависит от этого.**

---

## 4. Что НЕ делать (единогласно все источники)

| Не делать | Почему |
|-----------|--------|
| Добавлять ещё ML-модели | Переобучение, не объяснить убытки |
| Ещё 10 guard'ов без scoring | Уже 95% SKIP |
| Править 3 копии `__init__` в legacy | Техдолг без пользы для unified |
| WebSocket «вслепую» без кэша | Сложность без тестов snapshot+delta |
| Увеличивать депозит до 100+ сделок | Оба review предупреждают |

---

## 5. План тестирования (сквозной)

| Уровень | Что | Срок |
|---------|-----|------|
| Unit | RiskGuard, retest_watchlist, entry_pipeline score | Этапы 2–3 |
| Integration | 24h unified на testnet / малый депозит | После этапа 1 |
| Paper | `signal_only` + virtual trades SupervisorV4 | Уже есть, усилить отчёты |
| Soak | 72h memory + API counters | После этапа 1 |
| A/B | strict vs balanced preset | После этапа 3 |

Метрики успеха за 3 месяца:
- Profit factor > 1.2 на virtual/paper
- Max daily loss не срабатывает чаще 2 раз/нед
- SKIP rate 40–70% (не 95%)
- 0 rate-limit ban за месяц

---

## 6. Связь веток Git

| Ветка | Назначение |
|-------|------------|
| `12.06.26-OPT-ALL` | Ежедневные фиксы, config, деплой на сервер |
| `12.06.26-ALGO` | Архитектура, этапы 1–8, roadmap, крупные фичи |
| Слияние | ALGO → OPT-ALL после тестов каждого этапа |

Деплой на DigitalOcean — **только OPT-ALL** после вашего подтверждения.  
ALGO — разработка и тесты.

---

## 7. Рекомендуемый порядок старта (следующий шаг)

1. **Этап 1** — доработка `api_cache` + lazy orderbook (максимум пользы, минимум риска)
2. **Этап 2** — retest watchlist (вы просили «вход после ретеста» — это закроет 80% проблемы)
3. **Этап 3** — scoring вместо бинарных guard'ов

После каждого этапа: merge в `ДД.ММ.ГГ-OPT-ALL`, деплой, 48–72 ч наблюдение.

---

*Документ подготовлен: 12.06.2026. Ветка: `12.06.26-ALGO`.*
