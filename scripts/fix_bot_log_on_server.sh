#!/usr/bin/env bash
# Применить запись логов в bot.log для PRD-SCALP (или укажите каталог первым аргументом).
set -euo pipefail
REPO="${1:-$HOME/PRD-SCALP}"
cd "$REPO"
python3 scripts/fix_bot_log_filehandler.py --repo "$REPO"
echo "Перезапуск сервиса (если основной бот из этого каталога):"
echo "  sudo systemctl restart trading_bot.service"
