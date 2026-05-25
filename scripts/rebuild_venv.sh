#!/bin/bash
# Пересоздать venv на Linux-сервере (если pip: cannot execute / required file not found).
# Запуск: cd ~/PRD-BOT-ALL && bash scripts/rebuild_venv.sh
# Принудительно: FORCE_VENV_REBUILD=1 bash scripts/rebuild_venv.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REQ_FILE="$ROOT/requirements-unified.txt"
REQ_HASH_FILE="$ROOT/venv/.requirements-unified.sha256"
current_hash=""
if [ -f "$REQ_FILE" ]; then
  current_hash="$(sha256sum "$REQ_FILE" | awk '{print $1}')"
fi

if [ "${FORCE_VENV_REBUILD:-0}" != "1" ] && [ -x ./venv/bin/python3 ]; then
  if ./venv/bin/python3 -c "import telethon, aiohttp, yaml, telegram" 2>/dev/null; then
    if [ -n "$current_hash" ] && [ -f "$REQ_HASH_FILE" ] && [ "$(cat "$REQ_HASH_FILE")" = "$current_hash" ]; then
      echo "=== venv OK (зависимости не менялись) — пересборка пропущена ==="
      echo "  Принудительно: FORCE_VENV_REBUILD=1 bash scripts/rebuild_venv.sh"
      exit 0
    fi
    if [ -n "$current_hash" ]; then
      echo "=== venv: обновление pip-пакетов (requirements изменился) ==="
      ./venv/bin/python3 -m pip install -q -r requirements-unified.txt
      echo "$current_hash" > "$REQ_HASH_FILE"
      ./venv/bin/python3 -c "import telethon; print('telethon', telethon.__version__)"
      echo "core deps OK"
      exit 0
    fi
  fi
fi

echo "=== PRD-BOT-ALL: полная пересборка venv ==="
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
  # Оставляем только 1 последний бэкап (старые удаляем тихо)
  ls -1dt venv.bak.* 2>/dev/null | tail -n +2 | xargs -r rm -rf
fi

"$PYBOOT" -m venv venv
./venv/bin/python3 -m pip install --upgrade pip wheel
./venv/bin/pip install -r requirements-unified.txt
if [ -n "$current_hash" ]; then
  echo "$current_hash" > "$REQ_HASH_FILE"
fi

echo ""
./venv/bin/python3 -c "import telethon; print('telethon', telethon.__version__)"
./venv/bin/python3 -c "import aiohttp, yaml, telegram; print('core deps OK')"

echo ""
echo "=== Готово ==="
echo "Перезапуск служб:"
echo "  sudo systemctl restart trading_bot"
echo "  sudo systemctl restart telegram_signal_agent"
