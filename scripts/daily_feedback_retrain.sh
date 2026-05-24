#!/usr/bin/env bash
# Ежедневное переобучение transformer: стоп trading_bot → train → старт бота.
# Cron:  0 0 * * * root /root/PRD-BOT-ALL/scripts/daily_feedback_retrain.sh
# Systemd: deploy/feedback-retrain.timer (00:00 UTC)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_DIR}"

LOG_DIR="${REPO_DIR}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/daily_feedback_retrain.log"

SYSTEMD_TRADING="${PRD_TRADING_SERVICE:-trading_bot}"
RESTART_ON_EXIT=1

log() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
}

exec >>"${LOG_FILE}" 2>&1

log "===== daily_feedback_retrain start repo=${REPO_DIR} ====="

if [[ -x "${REPO_DIR}/venv/bin/python" ]]; then
  PY="${REPO_DIR}/venv/bin/python"
else
  PY="$(command -v python3)"
fi

restart_trading_bot() {
  if [[ "${RESTART_ON_EXIT}" != "1" ]]; then
    return 0
  fi
  log "systemctl start ${SYSTEMD_TRADING}"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl start "${SYSTEMD_TRADING}" || log "WARN: start ${SYSTEMD_TRADING} failed"
  fi
}

trap restart_trading_bot EXIT

if command -v systemctl >/dev/null 2>&1; then
  log "systemctl stop ${SYSTEMD_TRADING}"
  systemctl stop "${SYSTEMD_TRADING}" || log "WARN: stop ${SYSTEMD_TRADING} (maybe already stopped)"
  sleep 4
else
  log "WARN: systemctl not found — stop trading_bot manually if it runs"
fi

if ! "${PY}" -c "import torch" 2>/dev/null; then
  log "torch missing — installing CPU build (one-time, may take several minutes)"
  "${PY}" -m pip install -q --upgrade pip
  "${PY}" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
fi

if ! "${PY}" -c "import torch" 2>/dev/null; then
  log "ERROR: torch still not available after install"
  exit 1
fi

log "running feedback_retrain_once.py"
set +e
"${PY}" "${REPO_DIR}/scripts/feedback_retrain_once.py"
RETRAIN_EC=$?
set -e
log "feedback_retrain_once exit code=${RETRAIN_EC}"

if [[ "${RETRAIN_EC}" -eq 0 ]]; then
  log "retrain OK"
else
  log "retrain FAILED (bot will still restart)"
fi

exit "${RETRAIN_EC}"
