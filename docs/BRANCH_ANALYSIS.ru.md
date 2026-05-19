# Анализ веток PRD-BOT-03-26 (83 ветки)

Репозиторий: https://github.com/gogi-eng/PRD-BOT-03-26.git  
Дата анализа: май 2026.

## Семейства веток

### 1. `*_ScalpBot` (~35 веток, апрель–май 2026)

**Суть:** основной торговый бот для скальпинга на Bybit linear.

**Сильные стороны:**
- `bot/trading_bot.py` + миксины (`entry_exec`, `position_loop`, `guards`, `liquidation`)
- `engine/entry_engine.py`, `exit_engine.py`, `execution_engine.py`
- `engine/risk_manager.py` — единый риск (дневной лимит, серия убытков, размер позиции)
- `exchange/bybit_client.py` — полный async API v5
- `agents/multi_agent_manager.py` — trend/scalp/meanrev/breakout по режиму рынка
- `analysis/*` — funding, ликвидность, корреляции

**Последняя на момент анализа:** `18.05.26_ScalpBot` (проверьте на GitHub актуальнее).

**Брать для unified:** торговый движок, риск, Bybit-клиент, multi-agent.

---

### 2. `*_PRD-TELEGRAM-AGENT` (~10 веток)

**Суть:** мониторинг Telegram-каналов, парсинг сигналов, панель; торговля через `--trading-bot`.

**Сильные стороны:**
- `telegram_agent/signal_parse.py`, `risk_pipeline.py`, `execution_limits.py`
- `telegram_agent/signal_agent_panel.py` — UI/статус
- `scripts/telegram_signal_agent.py` — долгоживущий агент
- `main.py` — раздельный режим: агент сигналов vs торговый бот
- PID-lock, watchdog-скрипты, systemd unit

**Последняя:** `17.05.26_PRD-TELEGRAM-AGENT`.

**Брать для unified:** приём внешних сигналов, лимиты исполнения, отчётность по каналам.

---

### 3. `*_World_Agent` (07–08.05.26)

**Суть:** «мировой» контекст — аналитика рынка + feed.

**Сильные стороны:**
- `telegram_agent/world_feed.py`
- расширенный `analysis/` (те же модули, что ScalpBot)

**Брать:** обогащение сигналов макро/режимом рынка (`market_regime.py`).

---

### 4. `08.05.26_AutoFleet`

**Суть:** управление несколькими инстансами/стратегиями (fleet).

**Брать:** идея распределения капитала между стратегиями (`engine/capital_allocator.py`, `fund_lite/`).

---

### 5. `10.05.26_Bot_New`

**Суть:** упрощённая точка входа только для торговли (`main.py` → `TradingBot`).

**Брать:** простой запуск без Telegram-агента, если нужен только скальп.

---

### 6. `11.04.26_OPTIMUS`

**Суть:** экспериментальная ветка OPTIMUS (мета-стек / оптимизация).

**Брать:** осторожно, после code review; возможны `meta_controller`, RL-модули.

---

### 7. `03.04.26-main-phase1-refactor` + `main`

**Суть:** рефакторинг и базовая линия.

**Брать:** структуру каталогов, `CONFIG_GUIDE.md`, тесты в `backend/tests/`.

---

### 8. Ветки `config-2026-*`

**Суть:** снимки конфигурации под дату и профиль (`@TELEGRAM_AGENT`, `PRD-SCALP`).

**Брать:** готовые `config.yaml` как шаблон — не слепо копировать API-ключи.

---

### 9. Прочие

| Ветка | Комментарий |
|-------|-------------|
| `15–17.05.26_PRD-TELEGRAM-SCORE` | Скоринг каналов/сигналов |
| `feature/telegram-paid-signals-bybit` | Платные сигналы |
| `Polymarket`, `feature/polymarket-autobet` | Не Bybit — не смешивать |
| `cursor/*` | Временные ветки Cursor |

---

## Рекомендуемая база для production

```
prd-bot-source @ 17.05.26_PRD-TELEGRAM-AGENT  (или новее ScalpBot + merge telegram)
       +
prd-unified-agent  (этот проект — оркестрация, отчёты, безопасное self-tune)
```

## Что НЕ переносить целиком

- `bot/main.py` ~237 KB — монолит; использовать `bot/trading_bot.py` + engine
- Дублирующие risk-модули из старых веток (`core/risk_manager` и т.д.) — только `engine/risk_manager.py`
- Ветки Polymarket — отдельный продукт

## Сравнение: лучшее в каждой линии

| Компонент | Лучший источник |
|-----------|-----------------|
| Bybit API | `exchange/bybit_client.py` (ScalpBot / TELEGRAM-AGENT) |
| Риск | `engine/risk_manager.py` |
| Вход/выход | `engine/entry_engine.py`, `exit_engine.py` |
| Собственные сигналы | `agents/multi_agent_manager.py` |
| Telegram сигналы | `telegram_agent/*`, `scripts/telegram_signal_agent.py` |
| Эволюция стратегий | `evolution/orchestrator.py` (offline, не live-patch) |
| Self-modify live | **Новый** `prd_agent/evolution/self_improver.py` с sandbox |
