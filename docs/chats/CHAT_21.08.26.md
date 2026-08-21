# Чат 21.08.26 — отчёты дневного лимита, AI-Trader, маркет-сканер, трейлинг SL, схема лонг+шорт

## Контекст

- Бот PRD-BOT-ALL Bybit + Telegram, репо gogi-eng/PRD-BOT-03-26
- Пользователь: инженер ПБ, не программист, цель стабильной автоторговли

## Исправление двухчасовых отчётов (дневной лимит)

- Проблема: отчёт писал «дневной лимит убытка 6.2% — торговля заблокирована» при P&L на бирже +91.78 USD
- Причина: внутренний счётчик риска не сбрасывал DAILY_LOSS после восстановления PnL; отчёт смешивал внутренний блок и биржевой PnL
- Фикс: reconcile_from_closed_rows, _maybe_clear_daily_loss_stop, сверка с Bybit, PnL сегодня (биржа UTC) в bi_hourly
- Ветка/коммиты упоминались: 30.05.26-OPT-ALL (1b3d647 и др.)

## AI-Trader спам в Telegram

- Служба ai4trade_telegram_notify слала слишком часто
- Фикс: telegram_min_interval_sec: 1800 (раз в 30 мин), max_messages_per_poll: 1
- Коммит 66ede8a

## Маркет-сканер без сигналов

- Сканер был выключен / жил только в telegram_signal_agent
- Фикс: market_scanner в unified bot, min_score 58, ветка 30.05.26-OPT-ALL d2e1ed7

## Трейлинг SL «сразу в безубыток»

- Проверка ветки 01.06.26-OPT-ALL
- Код уже имеет барьер activation_pct; early_breakeven_enabled: false
- На сервере пользователь поставил trailing_activation_pct: 1.8 (normal) и 1.5 (pump_dump)
- grep подтвердил защиту в position_steward.py

## Анализ схемы лонг+шорт одного символа

- Схема TP 150% / SL 120% с «гарантией +20%» — ложная: стоп убыточной ноги срабатывает раньше, нет арбитража
- Вариант TP=SL: математически ~0 минус комиссии и фандинг
- Параметры для макс. шанса прибыли (если тестировать): асимметрия TP≈1.6–2× пути до SL другой ноги, плечо 3–5×, вход только в тренд, управление оставшейся ногой после первого SL, таймаут пары; это НЕ гарантия и плохо как основа пенсионной стратегии

## Деплой (справочно)

```bash
cd /root/PRD-BOT-ALL
git fetch origin
git checkout 01.06.26-OPT-ALL   # или актуальная OPT-ALL
git reset --hard origin/<ветка>
bash scripts/install_production_config.sh
sudo systemctl restart trading_bot
```

## Примечание по репозиторию

- На origin (gogi-eng/PRD-BOT-03-26) ветки 01.06.26-OPT-ALL и 30.05.26-OPT-ALL на момент сохранения чата через git ls-remote --heads не найдены; актуальные ветки — серии *-PRD-BOT-ALL / *-AGENT-WORLD и main.
- Файл пересохранён 21.08.26 (UTF-8), чтобы гарантировать полный текст сводки на GitHub.

## Hedge Pair 21.08.26 (дополнение к чату)

> Ранее в этом файле: суточные отчёты убытков, AI-Trader throttle, маркет-сканер, trailing SL, анализ long+short. Ниже — продолжение за сегодня про hedge-стратегию.

### Что сделали
- Стратегия Trend-Continuation Hedge Pair запушена в прод-ветку **21.08.26-OPT-ALL**
- Коммиты (примерно): 5c71704 (стратегия+тесты), e2356d3 (только fallback без других сигналов), 16d32df (live-открытие ордеров)

### Требование пользователя
- Хедж должен быть в общем анализе бота
- Выбирать ситуацию **только когда других сигналов нет** (`only_when_no_other_signals: true`)
- Не только лог «would open» — **реально открывать** ордера (`execute: true`)

### Как работает сейчас
1. Сначала collect_all (own/TA/telegram/whale...)
2. Если есть сигналы — обычная торговля, хедж молчит
3. Если сигналов нет + can_trade + нет открытой пары — fallback hedge
4. Открывает long (positionIdx=1) + short (positionIdx=2) с SL/TP
5. Если одна нога не открылась — rollback (закрыть успешную)
6. Каждый цикл manage: close / move_sl / flatten после первого SL

### Тесты бэктеста (ориентир)
- TP=SL → минус на комиссиях (~−0.24%)
- Continuation → плюс (~+0.40%)
- Reversal → минус (~−1.84%)
- Pytest hedge: 11 passed (после live-open)

### Важно
- Нужен Bybit **Hedge Mode**
- Гарантии прибыли нет; плюс только при продолжении тренда после первого SL
- Отключить: hedge_pair.enabled: false

### Деплой
```bash
cd /root/PRD-BOT-ALL
git fetch origin
git checkout 21.08.26-OPT-ALL
git reset --hard origin/21.08.26-OPT-ALL
bash scripts/install_production_config.sh
sudo systemctl restart trading_bot
```

Docs link: docs/strategies/HEDGE_PAIR_21.08.26.md on branch 21.08.26-OPT-ALL
