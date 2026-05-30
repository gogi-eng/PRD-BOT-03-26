#!/usr/bin/env bash
# Установка Kronos для PRD-BOT-ALL (venv + клон репозитория при необходимости)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KRONOS_HOME="${KRONOS_HOME:-$(dirname "$ROOT")/Kronos-master}"
KRONOS_REPO="${KRONOS_REPO:-https://github.com/shiyu-coder/Kronos.git}"

echo "==> PRD-BOT-ALL: $ROOT"
echo "==> Kronos:      $KRONOS_HOME"

pick_python() {
  if [[ -x "$ROOT/venv/bin/python3" ]]; then
    echo "$ROOT/venv/bin/python3"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  echo "ERROR: python3 не найден" >&2
  exit 1
}

PY="$(pick_python)"

if [[ ! -d "$KRONOS_HOME/model" ]]; then
  echo "==> Kronos не найден — клонируем в $KRONOS_HOME"
  if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: нужен git (apt install git)"
    exit 1
  fi
  git clone --depth 1 "$KRONOS_REPO" "$KRONOS_HOME"
fi

if [[ ! -x "$ROOT/venv/bin/python3" ]]; then
  echo "==> venv не найден — создаём $ROOT/venv (нужен для pip на Ubuntu 24)"
  if ! python3 -m venv "$ROOT/venv" 2>/dev/null; then
    echo "ERROR: python3 -m venv failed. Установите: apt install python3-venv python3-full"
    exit 1
  fi
  "$ROOT/venv/bin/python3" -m pip install --upgrade pip wheel
  if [[ -f "$ROOT/requirements-unified.txt" ]]; then
    "$ROOT/venv/bin/python3" -m pip install -r "$ROOT/requirements-unified.txt"
  fi
  PY="$ROOT/venv/bin/python3"
fi

echo "==> Python: $($PY --version) ($PY)"
echo "==> pip: Kronos + torch (CPU на Linux)..."

"$PY" -m pip install -r "$ROOT/requirements-kronos.txt"
"$PY" -m pip install -r "$KRONOS_HOME/requirements.txt"

# CPU-only torch на сервере без GPU (меньше RAM)
if [[ "$(uname -s)" == "Linux" ]]; then
  "$PY" -m pip install torch --index-url https://download.pytorch.org/whl/cpu || \
    "$PY" -m pip install 'torch>=2.0.0'
else
  "$PY" -m pip install 'torch>=2.0.0'
fi

if [[ -f "$ROOT/.env" ]]; then
  grep -q '^KRONOS_HOME=' "$ROOT/.env" 2>/dev/null || echo "KRONOS_HOME=$KRONOS_HOME" >> "$ROOT/.env"
else
  echo "KRONOS_HOME=$KRONOS_HOME" >> "$ROOT/.env"
fi

export KRONOS_HOME
echo "==> тест прогноза BTC (первый раз скачает модель ~100MB)..."
cd "$ROOT"
"$PY" scripts/kronos_bybit_forecast.py --symbol BTCUSDT

echo ""
echo "==> Готово."
echo "  Прогноз:  $PY scripts/kronos_bybit_forecast.py"
echo "  Telegram: $PY scripts/kronos_bybit_forecast.py --telegram"
echo "  KRONOS_HOME=$KRONOS_HOME записан в .env"
