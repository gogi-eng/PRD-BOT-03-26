#!/usr/bin/env bash
# Остановка / запуск systemd-ботов на неторговых окнах.
#   bash scripts/trading_hours_ctl.sh stop prod
#   bash scripts/trading_hours_ctl.sh start world
set -euo pipefail

ACTION="${1:?usage: trading_hours_ctl.sh stop|start prod|world}"
ENV="${2:?usage: trading_hours_ctl.sh stop|start prod|world}"

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

case "$ACTION" in
  stop)
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
