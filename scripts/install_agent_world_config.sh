#!/usr/bin/env bash
# Конфиг песочницы AGENT-WORLD (субаккаунт, без конфликта Telegram-кнопок с основным ботом).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SRC="${ROOT}/deploy/config.agent_world_sandbox.yaml"
DST="${ROOT}/config.yaml"
if [[ ! -f "$SRC" ]]; then
  echo "Нет $SRC"
  exit 1
fi
if [[ -f "$DST" ]]; then
  cp -a "$DST" "${DST}.bak.$(date +%Y%m%d_%H%M%S)"
fi
cp -a "$SRC" "$DST"
chmod 600 "$DST" 2>/dev/null || true
PYTHON=""
for cand in venv/bin/python3 venv/bin/python .venv/bin/python3; do
  if [[ -x "${ROOT}/${cand}" ]]; then
    PYTHON="${ROOT}/${cand}"
    break
  fi
done
if [[ -n "$PYTHON" ]]; then
  "$PYTHON" -m pip install -q 'pydantic>=2.6.4'
  "$PYTHON" "${ROOT}/scripts/validate_config_yaml.py"
  "$PYTHON" "${ROOT}/scripts/verify_live_sizing_config.py" --profile agent_world --config "$DST"
else
  python3 -m pip install -q 'pydantic>=2.6.4' 2>/dev/null || true
  python3 "${ROOT}/scripts/validate_config_yaml.py"
  python3 "${ROOT}/scripts/verify_live_sizing_config.py" --profile agent_world --config "$DST"
fi
echo "OK: AGENT-WORLD sandbox config → $DST"
echo "Проверьте .env: BYBIT_API_KEY/SECRET = ключи СУБАККАУНТА (не основного счёта)."
