#!/bin/bash
# Установка telethon и проверка коллектора Telegram. Запуск на сервере:
#   bash scripts/install_telegram_collector.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/venv/bin/python3"
if [ ! -x "$PY" ]; then
  PY="${ROOT}/.venv/bin/python3"
fi
if [ ! -x "$PY" ]; then
  echo "venv не найден. Запустите: bash scripts/rebuild_venv.sh"
  exit 1
fi

if ! "$PY" -m pip --version >/dev/null 2>&1; then
  echo "pip в venv сломан. Запустите: bash scripts/rebuild_venv.sh"
  exit 1
fi

echo "=== pip install telethon ==="
"$PY" -m pip install -q 'telethon>=1.34.0'
"$PY" -c "import telethon; print('telethon OK', telethon.__version__)"

if ! grep -q '^telegram_signal_agent:' config.yaml 2>/dev/null; then
  echo ""
  echo "ВНИМАНИЕ: в config.yaml нет секции telegram_signal_agent."
  echo "Добавьте блок из config.telegram_agent.snippet.yaml (nano config.yaml)"
fi

if ! grep -q 'TELEGRAM_API_ID' .env 2>/dev/null; then
  echo ""
  echo "ВНИМАНИЕ: в .env нет TELEGRAM_API_ID / TELEGRAM_API_HASH."
  echo "Возьмите с https://my.telegram.org/apps или скопируйте .env со старого бота:"
  echo "  cp ~/PRD-SCALP/.env ~/PRD-BOT-ALL/.env   # если был ScalpBot"
fi

echo ""
echo "=== restart telegram_signal_agent ==="
if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^telegram_signal_agent.service'; then
  sudo systemctl restart telegram_signal_agent
  sleep 2
  systemctl is-active telegram_signal_agent && echo "service: active" || echo "service: FAILED — journalctl -u telegram_signal_agent -n 30"
else
  echo "Служба telegram_signal_agent не установлена."
fi

echo ""
echo "Первый вход в Telegram (если нет *.session):"
echo "  $PY scripts/telegram_signal_agent.py --once --limit 5"
