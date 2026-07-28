#!/usr/bin/env bash
# Hourly liquid pairs report + optional Telegram (signal OR why-not).
# Does NOT touch trading_bot / telegram_signal_agent.
#
# Usage (from cron or manually):
#   bash /root/LIQUID-PAIRS-REPORT/scripts/run_hourly_liquid_pairs.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${REPO_ROOT}/data/reports"
LOG_FILE="${LOG_DIR}/hourly_run.log"
PY_SCRIPT="${SCRIPT_DIR}/hourly_liquid_pairs_report.py"

mkdir -p "${LOG_DIR}"
chmod 755 "${LOG_DIR}" 2>/dev/null || true

log() {
  local line
  line="[$(date '+%Y-%m-%d %H:%M:%S %z')] $*"
  printf '%s\n' "$line" | tee -a "${LOG_FILE}"
}

cd "${REPO_ROOT}"

if [[ ! -f "${PY_SCRIPT}" ]]; then
  log "ERROR: missing script ${PY_SCRIPT}"
  exit 1
fi

# Load secrets from local .env only (never commit). Prefer this folder's .env.
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

PYTHON=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  log "ERROR: python3 not found"
  exit 1
fi

export LIQUID_PAIRS_TELEGRAM="${LIQUID_PAIRS_TELEGRAM:-1}"
export PYTHONUNBUFFERED=1

log "START hourly liquid pairs root=${REPO_ROOT} py=${PYTHON}"
set +e
"${PYTHON}" "${PY_SCRIPT}" --telegram >>"${LOG_FILE}" 2>&1
code=$?
set -e
if [[ "${code}" -ne 0 ]]; then
  log "ERROR: hourly_liquid_pairs_report.py exit code ${code}"
  exit "${code}"
fi
log "OK: report finished"
exit 0
