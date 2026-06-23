#!/usr/bin/env bash
# Применить настройки «Self-Improving Trading Agent» (по видео) для PRD-BOT советника.
# Запуск на VPS: bash ~/.hermes/scripts/apply-trading-agent-setup.sh
set -euo pipefail

HERMES_DIR="${HERMES_DIR:-$HOME/.hermes}"
cd "$HERMES_DIR"

mkdir -p "$HERMES_DIR/state/history"

echo "==> Hermes trading advisor setup (PRD-BOT + ZeroOne loop, read-only)"
hermes --version || { echo "hermes не найден в PATH"; exit 1; }

echo "==> doctor"
hermes doctor || true

echo "==> gateway install (systemd user service)"
hermes gateway install || true

echo "==> enable linger (бот живёт после выхода из SSH)"
if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$(whoami)" || true
fi

echo "==> start gateway"
hermes gateway start || systemctl --user start hermes-gateway || true

echo "==> status"
hermes gateway status || systemctl --user status hermes-gateway --no-pager -l || true

echo ""
echo "Готово. В Telegram вставьте текст из:"
echo "  ~/.hermes/prompts/hermes-briefing-handoff.ru.txt"
echo ""
echo "Или коротко:"
echo "  «/prd-bot-morning-brief»"
echo "  «Настрой cron: брифинг 08:30 и reflection воскресенье 20:00 Москва»"
echo ""
echo "Проверка логов: journalctl --user -u hermes-gateway -n 50 --no-pager"
