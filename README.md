# AGENT-WORLD / PRD-BOT (`prd_agent`)

Единый асинхронный торговый агент для линейных бессрочных фьючерсов **Bybit USDT Perpetual** (`v5 API`) с многоуровневым риск-менеджментом, сопровождением позиций, самообучением (`SupervisorV4` + `SelfImprover`) и управлением через Telegram.

---

## 1. Назначение и архитектура

Основа системы — пакет `prd_agent/` и оркестратор `UnifiedOrchestrator` (`prd_agent/engine/orchestrator.py`).

### Краткий путь сигнала (Signal Flow)
1. **Сбор сигналов (`SignalRouter.collect_all`, `prd_agent/signals/router.py`)**:
   - Локальный мульти-агентный ансамбль (`own_multi_agent`), сканер волатильности (`ta_volatility`), очередь Telegram/новостных сигналов (`TelegramInbox`, `agent_world`, `mirror_pump_dump_agent`) и поток крупных ликвидаций/OI (`WhaleNewsAgent`).
2. **Слияние и ранжирование (`merge_and_rank` + `passes_emit_gate`)**:
   - Сигналы одного направления по символу агрегируются с весами источников; тройка уровней `(entry, stop_loss, take_profit)` сохраняется атомарно от наиболее приоритетного источника с полными уровнями (`primary_source`).
3. **Предварительные гейты (`SupervisorV4` + `RiskGuard`)**:
   - Проверка глобальных стопов (`can_trade`: дневной лимит убытка с 00:00 МСК, сессионный лимит убытков на монету, серия убытков, `max_positions`, файл `kill_switch`) и расписания/режима `SupervisorV4` (`can_enter`: `NORMAL` / `DEFENSIVE` / `AGGRESSIVE`, чёрный список монет и часов, фильтр волатильности ATR).
4. **Планирование уровней (`_plan_order_levels`)**:
   - Построение структурных зон SMC / FVG / Order Block / BOS (`EntryEngineBridge`), привязка SL/TP к уровням поддержки/сопротивления (`execution_sr_zones`) и контроль минимального `RR` (`enforce_min_rr_levels`).
5. **Конвейер исполнения (`_maybe_execute`)**:
   - Проверки `TimesFMGate`, `RetestWatchlist` / `check_pullback_entry`, `EntryGuard` (контроль drift цены, выбор `Market` / `Limit`), `DerivativesEntryGuard` (фандинг, OI, Long/Short ratio), `ZoneCorridorPlay`, скоринг `evaluate_entry_pipeline` и `QualityGate`.
   - Построение полного слепка рынка (`build_entry_snapshot`), расчёт безопасного плеча (`TradeAdvisor.recommend_leverage` + `RiskGuard.max_safe_leverage_for_sl`), расчёт объёма с учётом `soft_score` и режима волатильности (`volatility_regime_sizing`), округление по шагам биржи и проверка минимального номинала `>= 5 USDT` (`prepare_order`).
6. **Сопровождение открытых позиций (`PositionSteward`, `LiquidationGuard`, `PortfolioProfitLock`)**:
   - Безубыток с учётом комиссий и фандинга (`BE+`), адаптивный и GARCH-трейлинг с округлением по `price_step`, защита от ликвидации (`liquidation_guard`), time-stop и опциональная защита общей прибыли портфеля (`profit_lock`).

Расписание мировых торговых сессий (МСК) вынесено в [`docs/sessions.md`](docs/sessions.md).

---

## 2. Установка и подготовка окружения

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements-unified.txt
```

### Конфигурация: `.env`, `config.yaml` и `data/runtime_config_overrides.yaml`
- **`.env`** — хранит все секреты (ключи API Bybit, токен Telegram-бота, ключи OpenRouter/DeepSeek). Никогда не коммитьте `.env` в Git.
  - `BYBIT_API_KEY` / `BYBIT_API_SECRET` — ключи торгового аккаунта (или субаккаунта для `AGENT-WORLD`).
  - `BYBIT_READ_API_KEY` / `BYBIT_READ_API_SECRET` — опциональные read-only ключи для мониторинга.
  - `TESTNET=true` — принудительное включение тестовой сети Bybit.
  - `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` — бот и чат для уведомлений и панели управления.
- **`config.yaml`** — базовый статический конфигурационный файл стратегии. Для работающего бота он является **read-only** (комментарии и форматирование сохраняются).
  - Для установки профиля песочницы `AGENT-WORLD`:
    ```bash
    bash scripts/install_agent_world_config.sh
    ```
- **`data/runtime_config_overrides.yaml`** — файл динамических рантайм-оверлеев. Все изменения из Telegram-панели (пресеты риска, включение/выключение трейлинга, `adopt_manual`) и авто-подстройка `SelfImprover` записываются сюда и автоматически накладываются поверх `config.yaml` в `load_config()`.

Проверка валидности `config.yaml` и оверлея перед запуском:
```bash
./venv/bin/python3 scripts/validate_config_yaml.py
```

---

## 3. Запуск (сначала на Testnet)

Перед запуском на реальном счёте обязательно проверьте работу на **Bybit Testnet** (`TESTNET=true` в `.env` или `bybit.testnet: true` в `config.yaml`, либо режим `bot.signal_only: true`):

```bash
# Диагностика подключения, баланса и фильтров без выставления ордеров:
./venv/bin/python3 scripts/diagnose_trading.py

# Запуск основного цикла unified-агента:
./venv/bin/python3 run_unified.py
```

---

## 4. Аварийная остановка (Kill-Switch) и панель Telegram

### Kill-Switch (мгновенная блокировка новых входов с диска)
Чтобы мгновенно запретить боту открывать новые позиции без остановки сопровождения уже открытых сделок, создайте файл-флаг:
```bash
mkdir -p data/kill_switch && touch data/kill_switch/STOP_TRADING
```
Для возобновления торговли удалите файл:
```bash
rm -f data/kill_switch/STOP_TRADING
```

### Панель управления в Telegram (`/panel` или `/start`)
Через встроенного `ControlBot` доступны:
- Статус счёта, открытых позиций и активных риск-блоков;
- Переключение режимов: **Пауза всех входов**, **Signal-only**, **Трейлинг ВКЛ/ВЫКЛ**, **Ручные сделки (adopt_manual)**;
- Выбор пресетов риска (**Консервативный** / **Нормальный** / **Агрессивный**) и **Откат config** (из резервной копии в `data/sandbox/`);
- Сброс дневного лимита убытка и серии стопов (`reset_daily_loss`, `reset_risk_stops`);
- Отчёты по дням, экспорт сделок в CSV, статистика лаборатории пропущенных сигналов (`SkippedSignalBacktester`) и `TimesFM 2.5`.

---

## 5. Тестирование

Запуск модульных тестов:
```bash
./venv/bin/python3 -m pytest backend/tests/test_p0_agent_world_risk_and_order_prep.py \
  backend/tests/test_p1_router_supervisor_orchestrator.py \
  backend/tests/test_p2_runtime_state_and_cleanup.py \
  backend/tests/test_day_entry_stops.py \
  backend/tests/test_supervisor_v4.py -v
```
