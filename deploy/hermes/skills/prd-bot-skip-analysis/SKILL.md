---
name: prd-bot-skip-analysis
description: Разбор почему PRD-BOT не входит в сделки (skip reasons, supervisor, quality gate, derivatives guard). Только анализ.
---

# Анализ пропусков входов PRD-BOT

## Источники

- `/root/PRD-BOT-ALL/bot.log` — grep `Skip`, `reject`, `DEFENSIVE`, `derivatives_`
- `/root/PRD-BOT-ALL/data/supervisor/skip_stats.json`
- `/root/AGENT-WORLD/bot.log` и skip_stats (песочница)

## Формат ответа (русский)

1. **Главный «убийца» сигналов** — одна причина с % (если видно из stats).
2. **Supervisor** — режим, блок часов UTC+3, символы.
3. **Что делать пользователю** — кнопки Telegram (пресет, сброс риска), **не** правки кода без просьбы.

## Запрещено

Торговать, менять config, отключать polling/scanner.
