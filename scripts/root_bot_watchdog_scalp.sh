#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Установка на сервере (один раз):
#   sudo cp scripts/root_bot_watchdog_scalp.sh /root/bot_watchdog_scalp.sh
#   sudo chmod +x /root/bot_watchdog_scalp.sh
#
# Зачем это вместо «своего» длинного скрипта?
#   Cron запускал любой «main.py» из папки SCALP за «уже работает» — в том числе
#   режим БЕЗ --trading-bot (телеграм-агент). Тогда торговый бот не поднимался.
#
#   Здесь мы НЕ изобретаем логику заново: вызывается ваш же
#   PRD-SCALP/scripts/bot_watchdog.sh — там есть flock (один процесс) и
#   переменная WATCHDOG_MAIN_ARGS.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

export WATCHDOG_MAIN_ARGS="--trading-bot"
exec bash /root/PRD-SCALP/scripts/bot_watchdog.sh /root/PRD-SCALP
