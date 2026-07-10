#!/usr/bin/env bash
# Остановка / запуск systemd-ботов на неторговых окнах.
# stop: сначала закрыть все позиции на бирже, затем systemctl stop.
#   bash scripts/trading_hours_ctl.sh stop prod
#   bash scripts/trading_hours_ctl.sh start world
set -euo pipefail

ACTION="${1:?usage: trading_hours_ctl.sh stop|start prod|world}"
ENV="${2:?usage: trading_hours_ctl.sh stop|start prod|world}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() {
  echo "[$(date -Iseconds)] trading_hours_ctl $*"
}

run_systemctl() {
  if [[ "${EUID}" -eq 0 ]]; then
    systemctl "$@"
  else
    sudo systemctl "$@"
  fi
}

case "$ENV" in
  prod)
    UNITS=(trading_bot telegram_signal_agent)
    ;;
  world)
    UNITS=(trading_bot_agent_world telegram_signal_agent_world)
    ;;
  *)
    echo "unknown env: $ENV (use prod or world)" >&2
    exit 1
    ;;
esac

close_all_positions() {
  local py=""
  if [[ -x "${REPO_DIR}/venv/bin/python3" ]]; then
    py="${REPO_DIR}/venv/bin/python3"
  elif [[ -x "${REPO_DIR}/venv/bin/python" ]]; then
    py="${REPO_DIR}/venv/bin/python"
  else
    py="$(command -v python3)"
  fi
  local cfg="${REPO_DIR}/config.yaml"
  if [[ ! -f "$cfg" ]]; then
    log "warn: no config at $cfg — skip close_all_positions"
    return 0
  fi
  log "closing all positions ($ENV) repo=$REPO_DIR"
  if ! "$py" "${SCRIPT_DIR}/close_all_positions.py" --config "$cfg" --reason "trading_hours_stop_${ENV}"; then
    log "warn: close_all_positions failed — continuing with systemd stop"
  fi
}

case "$ACTION" in
  stop)
    close_all_positions
    for u in "${UNITS[@]}"; do
      if run_systemctl is-active --quiet "$u" 2>/dev/null; then
        log "stopping $u ($ENV)"
        run_systemctl stop "$u" || log "warn: stop failed $u"
      else
        log "already stopped $u ($ENV)"
      fi
    done
    ;;
  start)
    for u in "${UNITS[@]}"; do
      if run_systemctl is-active --quiet "$u" 2>/dev/null; then
        log "already running $u ($ENV)"
      else
        log "starting $u ($ENV)"
        run_systemctl start "$u" || log "warn: start failed $u"
      fi
    done
    ;;
  *)
    echo "unknown action: $ACTION (use stop or start)" >&2
    exit 1
    ;;
esac
