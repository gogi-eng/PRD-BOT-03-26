#!/usr/bin/env bash
# Register cron jobs that auto-restart PRD-SCALP and PRD-LONG bots.
# Idempotent: old watchdog lines are replaced on each run.
set -euo pipefail

SCALP_DIR="${SCALP_DIR:-/root/PRD-SCALP}"
LONG_DIR="${LONG_DIR:-/root/PRD-LONG}"
CRON_EVERY_MIN="${CRON_EVERY_MIN:-* * * * *}"

TMP="$(mktemp)"

{
  crontab -l 2>/dev/null | grep -vF 'bot_watchdog.sh' || true
  echo "${CRON_EVERY_MIN} bash ${SCALP_DIR}/scripts/bot_watchdog.sh ${SCALP_DIR} >> ${SCALP_DIR}/reports/watchdog_cron.log 2>&1"
  echo "${CRON_EVERY_MIN} bash ${LONG_DIR}/scripts/bot_watchdog.sh ${LONG_DIR} >> ${LONG_DIR}/reports/watchdog_cron.log 2>&1"
  echo "@reboot sleep 30 && bash ${SCALP_DIR}/scripts/bot_watchdog.sh ${SCALP_DIR} >> ${SCALP_DIR}/reports/watchdog_cron.log 2>&1"
  echo "@reboot sleep 45 && bash ${LONG_DIR}/scripts/bot_watchdog.sh ${LONG_DIR} >> ${LONG_DIR}/reports/watchdog_cron.log 2>&1"
} > "${TMP}"

crontab "${TMP}"
rm -f "${TMP}"

echo "OK: watchdog cron installed for:"
echo "  - ${SCALP_DIR}"
echo "  - ${LONG_DIR}"
echo ""
echo "Check:"
echo "  crontab -l | grep bot_watchdog.sh"
