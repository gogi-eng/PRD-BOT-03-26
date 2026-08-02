#!/usr/bin/env bash
# Установка проверенного config.yaml с ветки (секреты из .env).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SRC="${ROOT}/deploy/config.production.yaml"
DST="${ROOT}/config.yaml"
if [[ ! -f "$SRC" ]]; then
  echo "Нет $SRC — сделайте: git fetch origin && git checkout 29.05.26-OPT-ALL && git pull"
  exit 1
fi
if [[ -f "$DST" ]]; then
  cp -a "$DST" "${DST}.bak.$(date +%Y%m%d_%H%M%S)"
  echo "Резервная копия: ${DST}.bak.*"
  # Оставляем только самый новый bak — диск не забивается старыми копиями.
  bash "${ROOT}/scripts/prune_config_backups.sh" "${DST}.bak." || true
fi
cp -a "$SRC" "$DST"
chmod 600 "$DST" 2>/dev/null || true
PY=""
if [[ -x "${ROOT}/venv/bin/python3" ]]; then
  PY="${ROOT}/venv/bin/python3"
elif [[ -x "${ROOT}/venv/bin/python" ]]; then
  PY="${ROOT}/venv/bin/python"
else
  PY="python3"
fi
"$PY" "${ROOT}/scripts/validate_config_yaml.py"
# Жёстко: после install live должен держать ×1.5 baseline (не 0.1 от старого auto-tune).
"$PY" "${ROOT}/scripts/verify_live_sizing_config.py" --profile production --config "$DST"
echo "OK: $DST установлен. Ключи Bybit/Telegram возьмутся из .env если пусто в yaml."

# Cron stop/start по неторговым окнам (UTC сервера, не CRON_TZ).
if [[ -f "${ROOT}/scripts/install_trading_hours_cron.sh" ]]; then
  WORLD_ARG=""
  if [[ -d /root/AGENT-WORLD ]]; then
    WORLD_ARG="--world-dir /root/AGENT-WORLD"
  fi
  bash "${ROOT}/scripts/install_trading_hours_cron.sh" --prod-dir "$ROOT" ${WORLD_ARG} || {
    echo "warn: install_trading_hours_cron.sh failed — выполните вручную" >&2
  }
fi
