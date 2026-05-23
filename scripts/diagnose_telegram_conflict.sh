#!/bin/bash
# Диагностика Conflict getUpdates (два polling на один TELEGRAM_TOKEN).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== 1. Процессы Python в PRD-BOT-ALL ==="
pgrep -af "$ROOT" 2>/dev/null || echo "(нет)"
echo ""
pgrep -af 'run_unified|telegram_signal_agent|signal_sales' 2>/dev/null || echo "(нет)"

echo ""
echo "=== 2. systemd ==="
for u in trading_bot telegram_signal_agent prd-unified scalp_bot; do
  if systemctl cat "$u" 2>/dev/null | head -1 | grep -q .; then
    echo "--- $u ---"
    systemctl show "$u" -p ActiveState,ExecStart 2>/dev/null || true
  fi
done

echo ""
echo "=== 3. config: control_panel_enabled ==="
grep -n 'control_panel_enabled' config.yaml 2>/dev/null || echo "(нет в config.yaml — добавьте telegram_signal_agent.control_panel_enabled: false)"

echo ""
echo "=== 4. venv / .venv ==="
[ -d venv ] && echo "venv: OK $(venv/bin/python3 --version 2>/dev/null)"
[ -d .venv ] && echo ".venv: ЕСТЬ (может быть второй бот!) $(.venv/bin/python3 --version 2>/dev/null)"

echo ""
echo "=== 5. Webhook Telegram (нужен TELEGRAM_TOKEN в .env) ==="
if [ -f .env ]; then
  # shellcheck disable=SC1091
  set -a
  source .env 2>/dev/null || true
  set +a
fi
TOK="${TELEGRAM_TOKEN:-}"
if [ -z "$TOK" ] && [ -f config.yaml ]; then
  TOK=$(grep -E '^\s*bot_token:' config.yaml | head -1 | sed 's/.*: *//' | tr -d "'\" " || true)
fi
if [ -n "$TOK" ]; then
  echo "deleteWebhook..."
  curl -sS "https://api.telegram.org/bot${TOK}/deleteWebhook?drop_pending_updates=true" | head -c 200
  echo ""
  echo "getWebhookInfo:"
  curl -sS "https://api.telegram.org/bot${TOK}/getWebhookInfo" | head -c 300
  echo ""
else
  echo "TELEGRAM_TOKEN не найден в .env / config.yaml"
fi

echo ""
echo "=== 6. Последний Conflict в bot.log ==="
grep -i conflict bot.log 2>/dev/null | tail -5 || echo "(нет)"

echo ""
echo "=== Рекомендация ==="
echo "  bash scripts/fix_telegram_conflict.sh"
echo "  sudo cp deploy/trading_bot.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload && sudo systemctl restart trading_bot telegram_signal_agent"
