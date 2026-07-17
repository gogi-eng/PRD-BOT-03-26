#!/usr/bin/env bash
# Неторговые окна: pre_block (закрыть убыточные) и опционально stop/start systemd.
# По умолчанию (stop_systemd: false): боты работают, block_entry_utc_hours режет только входы.
#   bash scripts/trading_hours_ctl.sh pre_block prod
#   bash scripts/trading_hours_ctl.sh stop prod   # pre_block + stop если stop_systemd: true
set -euo pipefail

ACTION="${1:?usage: trading_hours_ctl.sh pre_block|stop|start prod|world}"
ENV="${2:?usage: trading_hours_ctl.sh pre_block|stop|start prod|world}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_FILE="${TRADING_HOURS_CTL_LOG:-/root/log_trading_hours_ctl.log}"
LOCK_FILE="/var/run/trading_hours_ctl_${ENV}.lock"
STOP_FLAG="/var/run/prd_trading_hours_stopped_${ENV}"

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

log() {
  local msg="[$(date -Iseconds)] trading_hours_ctl $*"
  echo "$msg"
  echo "$msg" >>"$LOG_FILE"
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "skip: another instance running (env=$ENV action=$ACTION)"
  exit 0
fi

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

resolve_python() {
  if [[ -x "${REPO_DIR}/venv/bin/python3" ]]; then
    echo "${REPO_DIR}/venv/bin/python3"
  elif [[ -x "${REPO_DIR}/venv/bin/python" ]]; then
    echo "${REPO_DIR}/venv/bin/python"
  else
    command -v python3
  fi
}

load_flags() {
  local py cfg
  py="$(resolve_python)"
  cfg="${REPO_DIR}/config.yaml"
  STOP_SYSTEMD=0
  PRE_BLOCK_CLOSE=1
  SCHED_ENABLED=1
  if [[ ! -f "$cfg" ]]; then
    log "warn: no config at $cfg — defaults stop_systemd=0 pre_block=1"
    return 0
  fi
  while IFS='=' read -r key val; do
    case "$key" in
      stop_systemd) STOP_SYSTEMD="$val" ;;
      pre_block_close) PRE_BLOCK_CLOSE="$val" ;;
      sched_enabled) SCHED_ENABLED="$val" ;;
    esac
  done < <("$py" "${SCRIPT_DIR}/trading_hours_flags.py" --config "$cfg")
}

pre_block_close() {
  if [[ "${PRE_BLOCK_CLOSE:-1}" != "1" ]]; then
    log "pre_block_close disabled in config ($ENV)"
    return 0
  fi
  local py cfg
  py="$(resolve_python)"
  cfg="${REPO_DIR}/config.yaml"
  if [[ ! -f "$cfg" ]]; then
    log "warn: no config at $cfg — skip pre_block"
    return 0
  fi
  log "pre_block: close losers / keep profitable trend ($ENV) repo=$REPO_DIR"
  if ! "$py" "${SCRIPT_DIR}/trading_hours_pre_block.py" --config "$cfg" --reason "trading_hours_pre_block_${ENV}"; then
    log "warn: trading_hours_pre_block failed ($ENV)"
  fi
}

stop_systemd_units() {
  touch "$STOP_FLAG"
  for u in "${UNITS[@]}"; do
    if run_systemctl is-active --quiet "$u" 2>/dev/null; then
      log "stopping $u ($ENV)"
      run_systemctl stop "$u" || log "warn: stop failed $u"
    else
      log "already stopped $u ($ENV)"
    fi
  done
}

start_systemd_units() {
  rm -f "$STOP_FLAG"
  for u in "${UNITS[@]}"; do
    if run_systemctl is-active --quiet "$u" 2>/dev/null; then
      log "already running $u ($ENV)"
    else
      log "starting $u ($ENV)"
      run_systemctl start "$u" || log "warn: start failed $u"
    fi
  done
}

load_flags

case "$ACTION" in
  pre_block)
    log "BEGIN pre_block $ENV (entries blocked via block_entry_utc_hours; bots stay up)"
    pre_block_close
    log "END pre_block $ENV"
    ;;
  stop)
    log "BEGIN stop $ENV stop_systemd=${STOP_SYSTEMD:-0}"
    pre_block_close
    if [[ "${STOP_SYSTEMD:-0}" == "1" ]]; then
      stop_systemd_units
    else
      log "stop_systemd=false — боты НЕ останавливаем, только блок новых входов по часам"
      rm -f "$STOP_FLAG"
    fi
    log "END stop $ENV"
    ;;
  start)
    if [[ "${STOP_SYSTEMD:-0}" != "1" ]]; then
      log "start skipped: stop_systemd=false ($ENV)"
      exit 0
    fi
    log "BEGIN start $ENV"
    start_systemd_units
    log "END start $ENV"
    ;;
  *)
    echo "unknown action: $ACTION (use pre_block, stop or start)" >&2
    exit 1
    ;;
esac
