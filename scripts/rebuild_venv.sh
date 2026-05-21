#!/bin/bash
# Пересоздать venv на Linux-сервере (если pip: cannot execute / required file not found).
# Запуск: cd ~/PRD-BOT-ALL && bash scripts/rebuild_venv.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== PRD-BOT-ALL: пересборка venv ==="
echo "Папка: $ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Ошибка: python3 не найден. Установите: apt install python3 python3-venv python3-pip"
  exit 1
fi

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Системный Python: $(python3 --version)"

if [ -d venv ]; then
  BK="venv.bak.$(date +%Y%m%d_%H%M%S)"
  echo "Старый venv → $BK"
  mv venv "$BK"
fi

python3 -m venv venv
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
