#!/usr/bin/env bash
# Установка Kronos + зависимости для PRD-BOT-ALL
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KRONOS_HOME="${KRONOS_HOME:-$(dirname "$ROOT")/Kronos-master}"

echo "==> PRD-BOT-ALL: $ROOT"
echo "==> Kronos:      $KRONOS_HOME"

if [[ ! -d "$KRONOS_HOME/model" ]]; then
  echo "ERROR: не найден $KRONOS_HOME (ожидается Kronos-master)"
  exit 1
fi

python3 -m pip install -r "$KRONOS_HOME/requirements.txt"

grep -q '^KRONOS_HOME=' "$ROOT/.env" 2>/dev/null || echo "KRONOS_HOME=$KRONOS_HOME" >> "$ROOT/.env"

echo "==> тест прогноза BTC (первый запуск скачает модель с HuggingFace)..."
cd "$ROOT"
python3 scripts/kronos_bybit_forecast.py --symbol BTCUSDT

echo "==> готово. Добавьте в config.yaml секцию kronos (см. config.example.yaml)"
