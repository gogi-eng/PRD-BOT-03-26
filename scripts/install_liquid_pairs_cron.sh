#!/usr/bin/env bash
# Install/update cron for liquid pairs hourly report in a dedicated folder.
# Does NOT restart trading_bot / telegram_signal_agent.
#
#   bash scripts/install_liquid_pairs_cron.sh
#   bash scripts/install_liquid_pairs_cron.sh --dir /root/LIQUID-PAIRS-REPORT
#   bash scripts/install_liquid_pairs_cron.sh --remove
set -euo pipefail

MARKER_BEGIN="# BEGIN LIQUID-PAIRS-REPORT"
MARKER_END="# END LIQUID-PAIRS-REPORT"
REPO_DIR="/root/LIQUID-PAIRS-REPORT"
REMOVE=false

usage() {
  echo "Usage: $0 [--dir PATH] [--remove]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) REPO_DIR="${2:?}"; shift 2 ;;
    --remove) REMOVE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

strip_block() {
  awk -v beg="$MARKER_BEGIN" -v end="$MARKER_END" '
    $0 == beg { skip=1; next }
    $0 == end { skip=0; next }
    !skip { print }
  '
}

TARGET_USER="${SUDO_USER:-$USER}"

apply_crontab() {
  local tmp
  tmp="$(mktemp)"
  if [[ "$EUID" -eq 0 && "$TARGET_USER" != "root" ]]; then
    crontab -u "$TARGET_USER" -l 2>/dev/null | strip_block >"$tmp" || true
    cat >>"$tmp"
    crontab -u "$TARGET_USER" "$tmp"
  else
    crontab -l 2>/dev/null | strip_block >"$tmp" || true
    cat >>"$tmp"
    crontab "$tmp"
  fi
  rm -f "$tmp"
}

if [[ "$REMOVE" == true ]]; then
  if [[ "$EUID" -eq 0 && "$TARGET_USER" != "root" ]]; then
    crontab -u "$TARGET_USER" -l 2>/dev/null | strip_block | crontab -u "$TARGET_USER" - || true
  else
    crontab -l 2>/dev/null | strip_block | crontab - || true
  fi
  echo "Removed LIQUID-PAIRS-REPORT cron block"
  exit 0
fi

REPO_DIR="$(cd "$REPO_DIR" && pwd)"
RUN_SH="${REPO_DIR}/scripts/run_hourly_liquid_pairs.sh"
LOG_FILE="${REPO_DIR}/data/reports/hourly_run.log"

if [[ ! -f "$RUN_SH" ]]; then
  echo "error: missing $RUN_SH ? deploy folder first" >&2
  exit 1
fi

chmod +x "$RUN_SH" "${REPO_DIR}/scripts/hourly_liquid_pairs_report.py" 2>/dev/null || true
mkdir -p "${REPO_DIR}/data/reports"

# Every hour at minute 0 (server UTC). Still once per clock-hour in any TZ.
{
  echo "$MARKER_BEGIN"
  echo "# liquid pairs: hourly report + Telegram (dedicated folder, not trading bots)"
  echo "0 * * * * cd ${REPO_DIR} && /bin/bash ${RUN_SH} >>${LOG_FILE} 2>&1"
  echo "$MARKER_END"
} | apply_crontab

echo "Installed LIQUID-PAIRS-REPORT cron for ${REPO_DIR}"
echo "Preview:"
echo "$MARKER_BEGIN"
echo "0 * * * * cd ${REPO_DIR} && /bin/bash ${RUN_SH} >>${LOG_FILE} 2>&1"
echo "$MARKER_END"
