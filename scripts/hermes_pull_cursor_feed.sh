#!/usr/bin/env bash
# Подтянуть Hermes-брифинг с VPS (для Linux/macOS или cron на ПК)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="${HERMES_PULL_CONFIG:-$ROOT/scripts/hermes_pull_config.json}"

if [[ ! -f "$CFG" ]]; then
  echo "Скопируйте scripts/hermes_pull_config.example.json → hermes_pull_config.json" >&2
  exit 1
fi

SSH_HOST="${HERMES_SSH_HOST:-$(python3 -c "import json;print(json.load(open('$CFG'))['ssh_host'])")}"
REMOTE="${HERMES_REMOTE_MD:-$(python3 -c "import json;print(json.load(open('$CFG'))['remote_md'])")}"
LOCAL_REL="${HERMES_LOCAL_MD:-$(python3 -c "import json;print(json.load(open('$CFG'))['local_md'])")}"
LOCAL="$ROOT/$LOCAL_REL"
IDENTITY="${HERMES_SSH_IDENTITY:-$(python3 -c "import json;print(json.load(open('$CFG')).get('identity_file') or '')")}"

mkdir -p "$(dirname "$LOCAL")"
SCP_OPTS=()
[[ -n "$IDENTITY" && -f "$IDENTITY" ]] && SCP_OPTS+=(-i "$IDENTITY")
scp "${SCP_OPTS[@]}" "${SSH_HOST}:${REMOTE}" "$LOCAL"
echo "[$(date '+%F %T')] Обновлено: $LOCAL"
