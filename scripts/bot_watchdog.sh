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

# Один воркер на репо: иначе два cron'а в ту же минуту дают двойной OK/двойной старт.
LOCK_FILE="${REPO_DIR}/reports/bot_watchdog.lock"
exec 200>"${LOCK_FILE}"
flock -n 200 || exit 0

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
# Инициализация (venv, импорты) + запись bot.pid; при 1GB RAM 2s часто мало.
for _i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if is_running; then
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [WATCHDOG] RESTARTED: bot started in ${REPO_DIR}" >> "${WATCHDOG_LOG}"
    exit 0
  fi
done

echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [WATCHDOG] ERROR: failed to start bot in ${REPO_DIR} (is_running false after 10s; OOM?)" >> "${WATCHDOG_LOG}"
if [[ -f "${LOG_FILE}" ]]; then
  {
    echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') [WATCHDOG] tail bot.log:"
    tail -n 8 "${LOG_FILE}" || true
  } >> "${WATCHDOG_LOG}"
fi
exit 1
