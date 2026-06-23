#!/usr/bin/env bash
# Hermes: git pull Analise_Hermes → .cursor/HERMES_LIVE.md в PRD-BOT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRD_ROOT="${PRD_BOT_ROOT:-$(dirname "$SCRIPT_DIR")}"
HERMES_DIR="${HERMES_GITHUB_DIR:-$(dirname "$PRD_ROOT")/Analise_Hermes}"

if [[ ! -d "$HERMES_DIR/.git" ]]; then
  echo "Клонируйте: git clone https://github.com/gogi-eng/Analise_Hermes.git $HERMES_DIR" >&2
  exit 1
fi

cd "$HERMES_DIR"
git pull --rebase origin main 2>/dev/null || git pull --rebase origin master

mkdir -p "$PRD_ROOT/.cursor"
cp -f "$HERMES_DIR/HERMES_LIVE.md" "$PRD_ROOT/.cursor/HERMES_LIVE.md"
echo "[$(date '+%F %T')] Обновлено: $PRD_ROOT/.cursor/HERMES_LIVE.md"
