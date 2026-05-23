#!/bin/bash
# Убрать Conflict: terminated by other getUpdates request
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== 1. Остановка ВСЕХ процессов бота ==="
sudo systemctl stop trading_bot telegram_signal_agent prd-unified scalp_bot 2>/dev/null || true
sudo pkill -9 -f "$ROOT/run_unified" 2>/dev/null || true
sudo pkill -9 -f "$ROOT/scripts/telegram_signal_agent" 2>/dev/null || true
sudo pkill -9 -f "$ROOT/.venv" 2>/dev/null || true
sleep 4

if pgrep -af "$ROOT.*run_unified|telegram_signal_agent" 2>/dev/null; then
  echo "ОШИБКА: процессы ещё живы. Проверьте другой сервер или ПК с тем же TELEGRAM_TOKEN."
  pgrep -af run_unified || true
  exit 1
fi

echo "=== 2. Webhook off (polling only) ==="
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env 2>/dev/null || true
  set +a
fi
TOK="${TELEGRAM_TOKEN:-}"
if [ -n "$TOK" ]; then
  curl -sS "https://api.telegram.org/bot${TOK}/deleteWebhook?drop_pending_updates=true" >/dev/null || true
  echo "  deleteWebhook OK"
fi

echo "=== 3. Один venv (venv), не .venv ==="
if [ -d .venv ] && [ ! -L .venv ]; then
  mv .venv ".venv.disabled.$(date +%Y%m%d_%H%M%S)"
  echo "  .venv отключён (переименован)"
fi
if [ ! -x venv/bin/python3 ]; then
  echo "  создаём venv..."
  bash scripts/rebuild_venv.sh
fi

echo "=== 4. systemd (только venv/bin/python3) ==="
if [ -f deploy/trading_bot.service ]; then
  sudo cp deploy/trading_bot.service /etc/systemd/system/trading_bot.service
fi
if [ -f deploy/telegram_signal_agent.service ]; then
  sudo cp deploy/telegram_signal_agent.service /etc/systemd/system/telegram_signal_agent.service
fi
sudo systemctl daemon-reload

echo "=== 5. Старт: сначала collector, потом trading (один polling) ==="
sudo systemctl start telegram_signal_agent
sleep 4
sudo systemctl start trading_bot
sleep 5

echo ""
sudo systemctl status trading_bot --no-pager | head -10
echo ""
RECENT=$(grep -i conflict bot.log 2>/dev/null | tail -1 || true)
if [ -n "$RECENT" ]; then
  echo "ВНИМАНИЕ: в bot.log ещё есть Conflict:"
  echo "$RECENT"
  echo "Запустите: bash scripts/diagnose_telegram_conflict.sh"
  echo "Возможен второй VPS/ПК с тем же ботом @BotFather."
else
  echo "OK: новых Conflict в хвосте лога не видно."
fi
