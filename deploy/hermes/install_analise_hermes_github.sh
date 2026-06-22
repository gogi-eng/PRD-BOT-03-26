#!/usr/bin/env bash
# Один раз на VPS: клон Analise_Hermes + deploy key hint
set -euo pipefail
HERMES_DIR="${HERMES_GITHUB_DIR:-/root/Analise_Hermes}"
REPO="https://github.com/gogi-eng/Analise_Hermes.git"

if [[ ! -d "$HERMES_DIR/.git" ]]; then
  git clone "$REPO" "$HERMES_DIR"
fi

# Локальная идентификация git (только этот репозиторий, не --global)
git -C "$HERMES_DIR" config user.name "PRD-BOT Hermes"
git -C "$HERMES_DIR" config user.email "hermes-bot@users.noreply.github.com"

cat <<EOF

OK: $HERMES_DIR

Для git push с сервера настройте Deploy Key:
  1) ssh-keygen -t ed25519 -f /root/.ssh/analise_hermes_deploy -N ""
  2) GitHub → Analise_Hermes → Settings → Deploy keys → Add
  3) В $HERMES_DIR:
       git config core.sshCommand "ssh -i /root/.ssh/analise_hermes_deploy -o IdentitiesOnly=yes"

Тест публикации:
  cd /root/AGENT-WORLD
  export HERMES_GITHUB_DIR=$HERMES_DIR
  ./venv/bin/python3 scripts/hermes_cursor_feed.py --hours 336 --git-push --force

EOF
