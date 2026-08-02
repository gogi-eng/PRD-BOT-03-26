#!/usr/bin/env bash
# Безопасная очистка диска на сервере PRD-BOT / AGENT-WORLD.
#
# По умолчанию — DRY-RUN (только показывает, что удалит).
# Реальное удаление: CONFIRM=1 bash scripts/server_disk_cleanup.sh
#
# НЕ трогает: .env, live config.yaml, venv/.venv, data/trades, data/ledger
#             (торговые данные — только с PURGE_TRADE_DATA=1, по умолчанию выкл).
#
# Чистит:
#   - старые /root/config.yaml.bak.* и /root/config.agent_world.bak.* (keep 1)
#   - config.yaml.bak* внутри /root/PRD-BOT-ALL и /root/AGENT-WORLD (keep 1)
#   - /tmp/AGENT-WORLD-dump-* (keep 1 самый новый)
#   - __pycache__ и *.pyc в бот-папках
#   - опционально: journal vacuum (VACUUM_JOURNAL=1)
set -euo pipefail

CONFIRM="${CONFIRM:-0}"
VACUUM_JOURNAL="${VACUUM_JOURNAL:-0}"
PURGE_TRADE_DATA="${PURGE_TRADE_DATA:-0}"
KEEP_BAK="${KEEP_BAK:-1}"

ROOTS=(/root/PRD-BOT-ALL /root/AGENT-WORLD)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRUNE="${SCRIPT_DIR}/prune_config_backups.sh"

bytes_freed=0
actions=0

log() { echo "[cleanup] $*"; }

do_rm() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    return 0
  fi
  local sz=0
  if [[ -f "$path" ]]; then
    sz=$(stat -c%s "$path" 2>/dev/null || echo 0)
  elif [[ -d "$path" ]]; then
    sz=$(du -sb "$path" 2>/dev/null | awk '{print $1}' || echo 0)
  fi
  if [[ "$CONFIRM" != "1" ]]; then
    log "DRY-RUN would remove: $path ($(numfmt --to=iec "$sz" 2>/dev/null || echo "${sz}B"))"
  else
    rm -rf -- "$path"
    log "removed: $path ($(numfmt --to=iec "$sz" 2>/dev/null || echo "${sz}B"))"
  fi
  bytes_freed=$((bytes_freed + sz))
  actions=$((actions + 1))
}

keep_latest_glob() {
  # keep_latest_glob '/tmp/AGENT-WORLD-dump-*'
  local pattern="$1"
  local keep_n="${2:-1}"
  shopt -s nullglob
  local files=( $pattern )
  if ((${#files[@]} <= keep_n)); then
    return 0
  fi
  mapfile -t sorted < <(ls -1t $pattern 2>/dev/null || true)
  local i=0
  for f in "${sorted[@]}"; do
    i=$((i + 1))
    if ((i <= keep_n)); then
      log "keep: $f"
      continue
    fi
    do_rm "$f"
  done
}

echo "=== server_disk_cleanup ==="
echo "CONFIRM=${CONFIRM}  VACUUM_JOURNAL=${VACUUM_JOURNAL}  KEEP_BAK=${KEEP_BAK}"
df -h / | tail -1 || true
BEFORE_AVAIL=$(df -B1 --output=avail / | tail -1 | tr -d ' ')

# 1) Config bak в /root и в репозиториях
if [[ -x "$PRUNE" ]] || [[ -f "$PRUNE" ]]; then
  if [[ "$CONFIRM" == "1" ]]; then
    bash "$PRUNE" /root/config.yaml.bak. || true
    bash "$PRUNE" /root/config.agent_world.bak. || true
    for r in "${ROOTS[@]}"; do
      [[ -d "$r" ]] || continue
      bash "$PRUNE" "${r}/config.yaml.bak." || true
    done
  else
    log "DRY-RUN: would prune bak via prune_config_backups.sh (keep newest)"
    for p in /root/config.yaml.bak.* /root/config.agent_world.bak.* \
             /root/PRD-BOT-ALL/config.yaml.bak.* /root/AGENT-WORLD/config.yaml.bak.*; do
      [[ -e "$p" ]] || continue
      log "  candidate bak: $p"
    done
  fi
fi

# 2) Старые dump в /tmp (оставить самый свежий)
keep_latest_glob '/tmp/AGENT-WORLD-dump-*' 1
keep_latest_glob '/tmp/PRD-BOT-ALL-dump-*' 1

# 3) __pycache__ / *.pyc в бот-папках (не в venv — тоже можно, но экономия места)
for r in "${ROOTS[@]}"; do
  [[ -d "$r" ]] || continue
  while IFS= read -r -d '' d; do
    # не трогаем venv ради скорости/безопасности установки
    case "$d" in
      */venv/*|*/.venv/*) continue ;;
    esac
    do_rm "$d"
  done < <(find "$r" -type d -name '__pycache__' -print0 2>/dev/null || true)

  while IFS= read -r -d '' f; do
    case "$f" in
      */venv/*|*/.venv/*) continue ;;
    esac
    do_rm "$f"
  done < <(find "$r" -type f -name '*.pyc' -print0 2>/dev/null || true)
done

# 4) Опционально journal
if [[ "$VACUUM_JOURNAL" == "1" ]]; then
  if [[ "$CONFIRM" == "1" ]]; then
    journalctl --vacuum-size=200M || true
    log "journal vacuum to 200M done"
  else
    log "DRY-RUN would: journalctl --vacuum-size=200M"
  fi
fi

# 5) Явный запрет на trade data без флага
if [[ "$PURGE_TRADE_DATA" == "1" ]]; then
  log "WARN: PURGE_TRADE_DATA=1 — не реализовано намеренно (слишком опасно). Пропуск."
fi

AFTER_AVAIL=$(df -B1 --output=avail / | tail -1 | tr -d ' ')
DELTA=$((AFTER_AVAIL - BEFORE_AVAIL))
echo "=== summary ==="
echo "actions=${actions} estimated_bytes=${bytes_freed}"
echo "avail_before=${BEFORE_AVAIL} avail_after=${AFTER_AVAIL} delta=${DELTA}"
df -h / | tail -1 || true
if [[ "$CONFIRM" != "1" ]]; then
  echo ""
  echo "Это был DRY-RUN. Для удаления: CONFIRM=1 bash scripts/server_disk_cleanup.sh"
  echo "Опционально journal: CONFIRM=1 VACUUM_JOURNAL=1 bash scripts/server_disk_cleanup.sh"
fi
