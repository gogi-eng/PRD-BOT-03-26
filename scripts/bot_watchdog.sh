#!/usr/bin/env bash
# Auto-restart watchdog for one bot repository.
# Usage:
#   bash scripts/bot_watchdog.sh /root/PRD-SCALP
# If REPO_DIR is omitted, script uses parent directory of this script.
set -euo pipefail

REPO_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
MAIN_PATH="${REPO_DIR}/main.py"
PID_FILE="${REPO_DIR}/bot.pid"
LOG_FILE="${REPO_DIR}/bot.log"
WATCHDOG_LOG="${REPO_DIR}/reports/watchdog.log"

mkdir -p "${REPO_DIR}/reports"

if [[ ! -f "${MAIN_PATH}" ]]; then
  echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [WATCHDOG] ERROR: main.py not found in ${REPO_DIR}" >> "${WATCHDOG_LOG}"
  exit 1
fi

if [[ -x "${REPO_DIR}/venv/bin/python" ]]; then
  PYTHON_BIN="${REPO_DIR}/venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [WATCHDOG] ERROR: python interpreter not found for ${REPO_DIR}" >> "${WATCHDOG_LOG}"
  exit 1
fi

is_running() {
  # Prefer PID file created by bot itself.
  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(tr -dc '0-9' < "${PID_FILE}" || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      return 0
    fi
  fi
  # Fallback check by process command line.
  pgrep -f "${MAIN_PATH}" >/dev/null 2>&1
}

if is_running; then
  echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [WATCHDOG] OK: bot already running in ${REPO_DIR}" >> "${WATCHDOG_LOG}"
  exit 0
fi

cd "${REPO_DIR}"
nohup "${PYTHON_BIN}" main.py >> "${LOG_FILE}" 2>&1 &
sleep 2

if is_running; then
  echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [WATCHDOG] RESTARTED: bot started in ${REPO_DIR}" >> "${WATCHDOG_LOG}"
  exit 0
fi

echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [WATCHDOG] ERROR: failed to start bot in ${REPO_DIR}" >> "${WATCHDOG_LOG}"
exit 1
