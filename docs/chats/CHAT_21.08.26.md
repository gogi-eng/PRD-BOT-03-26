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
