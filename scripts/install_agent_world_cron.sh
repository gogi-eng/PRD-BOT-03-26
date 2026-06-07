#!/usr/bin/env bash
# Установка / удаление cron для AGENT-WORLD (RSS → scripts/agent_world.py).
# Использование:
#   sudo bash scripts/install_agent_world_cron.sh
#   sudo bash scripts/install_agent_world_cron.sh --repo-dir /root/PRD-BOT-ALL --every 10
#   sudo bash scripts/install_agent_world_cron.sh --remove

set -euo pipefail

MARKER_BEGIN="# BEGIN PRD-BOT agent_world RSS"
MARKER_END="# END PRD-BOT agent_world RSS"
REPO_DIR=""
EVERY_MIN=10
REMOVE=false

usage() {
  echo "Usage: $0 [--repo-dir PATH] [--every N_minutes] [--remove]" >&2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir) REPO_DIR="${2:?}"; shift 2 ;;
    --every) EVERY_MIN="${2:?}"; shift 2 ;;
    --remove) REMOVE=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -z "$REPO_DIR" ]] && REPO_DIR="$DEFAULT_REPO"
REPO_DIR="$(cd "$REPO_DIR" && pwd)"

if ! [[ "$EVERY_MIN" =~ ^[0-9]+$ ]] || [[ "$EVERY_MIN" -lt 1 || "$EVERY_MIN" -gt 59 ]]; then
  echo "error: --every must be 1–59" >&2
  exit 1
fi

if [[ ! -f "$REPO_DIR/scripts/agent_world.py" ]]; then
  echo "error: missing scripts/agent_world.py in $REPO_DIR" >&2
  exit 1
fi

if [[ -x "$REPO_DIR/venv/bin/python" ]]; then
  PYTHON="$REPO_DIR/venv/bin/python"
elif [[ -x "$REPO_DIR/venv/bin/python3" ]]; then
  PYTHON="$REPO_DIR/venv/bin/python3"
elif [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
  PYTHON="$REPO_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "error: no Python found" >&2
  exit 1
fi

TARGET_USER="${SUDO_USER:-$USER}"
LOG_DIR="$REPO_DIR/reports/world"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/agent_world_cron.log"
CRON_MIN="*/${EVERY_MIN}"

CRON_BLOCK="${MARKER_BEGIN}
${CRON_MIN} * * * * cd ${REPO_DIR} && ${PYTHON} ${REPO_DIR}/scripts/agent_world.py >> ${LOG_FILE} 2>&1
${MARKER_END}"

strip_block() {
  awk -v beg="$MARKER_BEGIN" -v end="$MARKER_END" '
    $0 == beg { skip=1; next }
    $0 == end { skip=0; next }
    !skip { print }
  '
}

if [[ "$REMOVE" == true ]]; then
  if [[ "$EUID" -eq 0 && "$TARGET_USER" != "root" ]]; then
    crontab -u "$TARGET_USER" -l 2>/dev/null | strip_block | crontab -u "$TARGET_USER" - || true
  else
    crontab -l 2>/dev/null | strip_block | crontab - || true
  fi
  echo "Removed agent_world cron block"
  exit 0
fi

if [[ "$EUID" -eq 0 && "$TARGET_USER" != "root" ]]; then
  ( crontab -u "$TARGET_USER" -l 2>/dev/null | strip_block; echo "$CRON_BLOCK" ) | crontab -u "$TARGET_USER" -
else
  ( crontab -l 2>/dev/null | strip_block; echo "$CRON_BLOCK" ) | crontab -
fi

echo "Installed agent_world cron every ${EVERY_MIN} min"
echo "Repo:   $REPO_DIR"
echo "Python: $PYTHON"
echo "Log:    $LOG_FILE"
