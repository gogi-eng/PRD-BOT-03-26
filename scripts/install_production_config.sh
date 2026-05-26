#!/usr/bin/env bash
# Установка проверенного config.yaml с ветки (секреты из .env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SRC="${ROOT}/deploy/config.production.yaml"
DST="${ROOT}/config.yaml"
if [[ ! -f "$SRC" ]]; then
  echo "Нет $SRC — сделайте git pull origin 26.05.26-ALL"
  exit 1
fi
if [[ -f "$DST" ]]; then
  cp -a "$DST" "${DST}.bak.$(date +%Y%m%d_%H%M%S)"
  echo "Резервная копия: ${DST}.bak.*"
fi
cp -a "$SRC" "$DST"
chmod 600 "$DST" 2>/dev/null || true
if [[ -x "${ROOT}/venv/bin/python3" ]]; then
  "${ROOT}/venv/bin/python3" "${ROOT}/scripts/validate_config_yaml.py"
else
  python3 "${ROOT}/scripts/validate_config_yaml.py"
fi
echo "OK: $DST установлен. Ключи Bybit/Telegram возьмутся из .env если пусто в yaml."
