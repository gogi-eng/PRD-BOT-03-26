---
name: prd-bot-morning-brief
description: Утренний брифинг по PRD-BOT (прод + AGENT-WORLD): позиции, supervisor, skip-причины, funding. Только чтение, без ордеров.
---

# Утренний брифинг PRD-BOT

## Когда использовать

Пользователь просит: «утренний брифинг», «что с ботом», «статус торгов», «morning brief», cron по расписанию.

## Запрещено

- Ордера на Bybit, изменение config.yaml, API-ключи биржи.

## Шаги

1. Проверить время: Europe/Moscow (UTC+3).
2. Прочитать хвост логов (если доступен терминал):
   - `tail -n 120 /root/PRD-BOT-ALL/bot.log`
   - `tail -n 80 /root/AGENT-WORLD/bot.log` (если есть)
3. Supervisor / skip stats:
   - `cat /root/PRD-BOT-ALL/data/supervisor/skip_stats.json` (если есть)
   - `cat /root/AGENT-WORLD/data/supervisor/skip_stats.json` (если есть)
4. Кратко: открытые позиции (из лога «open positions» / journalctl), режим supervisor (NORMAL/DEFENSIVE), топ-3 причины SKIP за последние часы.
5. Ответ **на русском**, простым языком, блоками:
   - Прод PRD-BOT-ALL
   - Песочница AGENT-WORLD (если лог есть)
   - Рекомендация (без давления): ждать / проверить часы / кнопки в Telegram бота

## Cron (если пользователь просит автоматизацию)

Расписание по умолчанию: **08:30 Europe/Moscow** в будни.

Пример запроса для cron: «Выполни skill prd-bot-morning-brief и пришли результат в Telegram».

После успешного брифинга — сохрани улучшения workflow в skill (self-improvement loop Hermes).
