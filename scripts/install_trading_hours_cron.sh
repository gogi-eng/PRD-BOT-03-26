#!/usr/bin/env bash
# Cron: stop ботов в начале неторгового окна, start за 5 мин до конца (из block_entry_utc_hours).
# Время в cron = UTC сервера (DigitalOcean), не МСК — CRON_TZ ненадёжен.
#
#   sudo bash scripts/install_trading_hours_cron.sh --prod-dir /root/PRD-BOT-ALL
#   sudo bash scripts/install_trading_hours_cron.sh --world-dir /root/AGENT-WORLD
#   sudo bash scripts/install_trading_hours_cron.sh --prod-dir /root/PRD-BOT-ALL --world-dir /root/AGENT-WORLD
#   sudo bash scripts/install_trading_hours_cron.sh --remove
set -euo pipefail

MARKER_BEGIN="# BEGIN PRD-BOT trading_hours"
MARKER_END="# END PRD-BOT trading_hours"
PROD_DIR=""
WORLD_DIR=""
REMOVE=false

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  echo "Usage: $0 [--prod-dir PATH] [--world-dir PATH] [--remove]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prod-dir) PROD_DIR="${2:?}"; shift 2 ;;
    --world-dir) WORLD_DIR="${2:?}"; shift 2 ;;
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

if [[ "$REMOVE" == true ]]; then
  if [[ "$EUID" -eq 0 && "$TARGET_USER" != "root" ]]; then
    crontab -u "$TARGET_USER" -l 2>/dev/null | strip_block | crontab -u "$TARGET_USER" - || true
  else
    crontab -l 2>/dev/null | strip_block | crontab - || true
  fi
  echo "Removed trading_hours cron block"
  exit 0
fi

if [[ -z "$PROD_DIR" && -z "$WORLD_DIR" ]]; then
  PROD_DIR="$DEFAULT_REPO"
fi

chmod +x "${SCRIPT_DIR}/trading_hours_ctl.sh" 2>/dev/null || true

CRON_LINES=()
CRON_LINES+=("$MARKER_BEGIN")
CRON_LINES+=("# server UTC cron — MSK times in comments (timezone_offset from config)")

add_env_cron() {
  local repo="$1"
  local env="$2"
  local cfg="${repo}/config.yaml"
  local py=""
  if [[ -x "${repo}/venv/bin/python3" ]]; then
    py="${repo}/venv/bin/python3"
  elif [[ -x "${repo}/venv/bin/python" ]]; then
    py="${repo}/venv/bin/python"
  else
    py="$(command -v python3)"
  fi
  if [[ ! -f "$cfg" ]]; then
    echo "warn: no config at $cfg — skip $env" >&2
    return 0
  fi
  while IFS= read -r line; do
    [[ -n "$line" ]] && CRON_LINES+=("$line")
  done < <("$py" "${SCRIPT_DIR}/trading_hours_schedule.py" --config "$cfg" --env "$env" --repo-dir "$repo" --print-cron)
}

[[ -n "$PROD_DIR" ]] && add_env_cron "$(cd "$PROD_DIR" && pwd)" prod
[[ -n "$WORLD_DIR" ]] && add_env_cron "$(cd "$WORLD_DIR" && pwd)" world

if [[ "${#CRON_LINES[@]}" -le 2 ]]; then
  echo "error: no cron lines generated (check config.yaml block_entry_utc_hours)" >&2
  exit 1
fi

# Ежедневно 00:05 МСК — пересчёт cron при смене DST в NY (21:05 UTC при offset=3).
REFRESH_REPO="${PROD_DIR:-$WORLD_DIR:-$DEFAULT_REPO}"
REFRESH_REPO="$(cd "$REFRESH_REPO" && pwd)"
REFRESH_PY=""
if [[ -x "${REFRESH_REPO}/venv/bin/python3" ]]; then
  REFRESH_PY="${REFRESH_REPO}/venv/bin/python3"
else
  REFRESH_PY="$(command -v python3)"
fi
REFRESH_CRON="$(
  cd "${REFRESH_REPO}" && PYTHONPATH="${REFRESH_REPO}" "$REFRESH_PY" -c "
from prd_agent.analysis.trading_hours_schedule import local_hhmm_to_utc_cron
print(local_hhmm_to_utc_cron('00:05', 3) + ' * * *')
"
)"
WORLD_ARG=""
[[ -n "$WORLD_DIR" ]] && WORLD_ARG="--world-dir $(cd "$WORLD_DIR" && pwd)"
PROD_ARG=""
[[ -n "$PROD_DIR" ]] && PROD_ARG="--prod-dir $(cd "$PROD_DIR" && pwd)"
CRON_LINES+=(
  "${REFRESH_CRON} cd ${REFRESH_REPO} && bash ${SCRIPT_DIR}/install_trading_hours_cron.sh ${PROD_ARG} ${WORLD_ARG} >> /root/log_trading_hours_cron_refresh.log 2>&1  # refresh 00:05 MSK"
)

CRON_LINES+=("$MARKER_END")

TMP="$(mktemp)"
if [[ "$EUID" -eq 0 && "$TARGET_USER" != "root" ]]; then
  crontab -u "$TARGET_USER" -l 2>/dev/null | strip_block >"$TMP" || true
else
  crontab -l 2>/dev/null | strip_block >"$TMP" || true
fi
printf '%s\n' "${CRON_LINES[@]}" >>"$TMP"
if [[ "$EUID" -eq 0 && "$TARGET_USER" != "root" ]]; then
  crontab -u "$TARGET_USER" "$TMP"
else
  crontab "$TMP"
fi
rm -f "$TMP"

echo "Installed trading_hours cron (${#CRON_LINES[@]} lines, server UTC)"
echo "Preview:"
printf '%s\n' "${CRON_LINES[@]}"
