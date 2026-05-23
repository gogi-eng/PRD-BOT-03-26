#!/bin/bash
# Убрать Conflict: terminated by other getUpdates request
set -euo pipefail
echo "=== Остановка всех процессов с polling бота ==="
sudo systemctl stop trading_bot telegram_signal_agent 2>/dev/null || true
pkill -f run_unified.py 2>/dev/null || true
pkill -f telegram_signal_agent.py 2>/dev/null || true
sleep 3
if pgrep -af 'run_unified|telegram_signal_agent' >/dev/null; then
  echo "Ещё работают:"
  pgrep -af 'run_unified|telegram_signal_agent' || true
  echo "Повторите: sudo pkill -9 -f run_unified; sudo pkill -9 -f telegram_signal_agent"
  exit 1
fi
echo "=== Старт служб (один polling на токен) ==="
sudo systemctl start telegram_signal_agent
sleep 2
sudo systemctl start trading_bot
sleep 3
sudo systemctl status trading_bot --no-pager | head -10
echo ""
echo "Проверка Conflict в логе (должно быть пусто):"
grep -i conflict /root/PRD-BOT-ALL/bot.log 2>/dev/null | tail -3 || echo "  OK — Conflict не найден"
