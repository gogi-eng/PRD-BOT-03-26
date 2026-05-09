#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Установка на сервере (один раз):
#   sudo cp scripts/root_bot_watchdog_new.sh /root/bot_watchdog_new.sh
#   sudo chmod +x /root/bot_watchdog_new.sh
#
# То же, что для SCALP: один стандартный watchdog из репозитория + только
# флаг --trading-bot, чтобы никогда случайно не поднять режим по умолчанию
# (телеграм-агент без флага).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

export WATCHDOG_MAIN_ARGS="--trading-bot"
exec bash /root/PRD-BOT-NEW/scripts/bot_watchdog.sh /root/PRD-BOT-NEW
