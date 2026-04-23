#!/usr/bin/env bash
# Install or refresh daily Bybit tickers snapshot in user crontab (Linux).
# Idempotent: replaces any existing line that references snapshot_bybit_tickers.py.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "${REPO}/reports"

# Prefer project venv; fall back to python3 on PATH.
if [[ -x "${REPO}/venv/bin/python" ]]; then
  PY="${REPO}/venv/bin/python"
elif [[ -n "${PYTHON:-}" && -x "${PYTHON}" ]]; then
  PY="${PYTHON}"
else
  PY="$(command -v python3)"
fi
if [[ -z "${PY}" || ! -x "${PY}" ]]; then
  echo "ERROR: no Python found (expected ${REPO}/venv/bin/python or python3)." >&2
  exit 1
fi

# Default: 00:05 UTC daily. Override: export CRON_SCHEDULE='10 3 * * *'
CRON_SCHEDULE="${CRON_SCHEDULE:-5 0 * * *}"
LOG_FILE="${REPO}/reports/ticker_snapshot_cron.log"
SNAP="${REPO}/scripts/snapshot_bybit_tickers.py"

if [[ ! -f "${SNAP}" ]]; then
  echo "ERROR: missing ${SNAP}" >&2
  exit 1
fi

CRON_LINE="${CRON_SCHEDULE} cd ${REPO} && ${PY} ${SNAP} >> ${LOG_FILE} 2>&1"

TMP="$(mktemp)"
{
  crontab -l 2>/dev/null | grep -vF 'snapshot_bybit_tickers.py' || true
  echo "${CRON_LINE}"
} > "${TMP}"
crontab "${TMP}"
rm -f "${TMP}"

echo "OK: crontab updated."
echo "  Schedule: ${CRON_SCHEDULE} (UTC)"
echo "  Command:  cd ${REPO} && ${PY} ${SNAP}"
echo "  Log:      ${LOG_FILE}"
echo ""
echo "Verify: crontab -l | grep snapshot_bybit_tickers"
