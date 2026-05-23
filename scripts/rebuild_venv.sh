#!/bin/bash
# Пересоздать venv на Linux-сервере (если pip: cannot execute / required file not found).
# Запуск: cd ~/PRD-BOT-ALL && bash scripts/rebuild_venv.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== PRD-BOT-ALL: пересборка venv ==="
echo "Папка: $ROOT"

# pyenv: команда python часто не настроена — используем python3
if command -v python3 >/dev/null 2>&1; then
  PYBOOT=python3
elif [ -n "${PYENV_ROOT:-}" ] && [ -x "${PYENV_ROOT}/shims/python3" ]; then
  PYBOOT="${PYENV_ROOT}/shims/python3"
elif command -v pyenv >/dev/null 2>&1; then
  pyenv global 3.11.9 2>/dev/null || true
  PYBOOT="$(pyenv which python3 2>/dev/null || pyenv which python 2>/dev/null || true)"
fi
if [ -z "${PYBOOT:-}" ] || ! command -v "$PYBOOT" >/dev/null 2>&1; then
  echo "Ошибка: python3 не найден."
  echo "  apt install python3 python3-venv python3-full"
  echo "  или: pyenv global 3.11.9"
  exit 1
fi

echo "Системный Python: $($PYBOOT --version)"

if [ -d venv ]; then
  BK="venv.bak.$(date +%Y%m%d_%H%M%S)"
  echo "Старый venv → $BK"
  mv venv "$BK"
fi

"$PYBOOT" -m venv venv
./venv/bin/python3 -m pip install --upgrade pip wheel
./venv/bin/pip install -r requirements-unified.txt

echo ""
./venv/bin/python3 -c "import telethon; print('telethon', telethon.__version__)"
./venv/bin/python3 -c "import aiohttp, yaml, telegram; print('core deps OK')"

echo ""
echo "=== Готово ==="
echo "Перезапуск служб:"
echo "  sudo systemctl restart trading_bot"
echo "  sudo systemctl restart telegram_signal_agent"
