#!/usr/bin/env bash
# Оставляет только самую новую резервную копию по префиксу.
# Использование:
#   bash scripts/prune_config_backups.sh /root/PRD-BOT-ALL/config.yaml.bak.
#   bash scripts/prune_config_backups.sh /root/config.agent_world.bak.
#
# Не трогает live config.yaml и .env.
set -euo pipefail

PREFIX="${1:-}"
if [[ -z "$PREFIX" ]]; then
  echo "usage: $0 /path/to/prefix." >&2
  exit 2
fi

shopt -s nullglob
files=( "${PREFIX}"* )
if ((${#files[@]} <= 1)); then
  if ((${#files[@]} == 1)); then
    echo "keep bak (only one): ${files[0]}"
  fi
  exit 0
fi

# Сортировка по mtime: новее сверху (GNU ls -t).
mapfile -t sorted < <(ls -1t "${PREFIX}"* 2>/dev/null || true)
if ((${#sorted[@]} <= 1)); then
  exit 0
fi

keep="${sorted[0]}"
removed=0
for f in "${sorted[@]:1}"; do
  [[ -f "$f" ]] || continue
  rm -f -- "$f"
  echo "removed old bak: $f"
  removed=$((removed + 1))
done
echo "kept bak: $keep (removed ${removed})"
