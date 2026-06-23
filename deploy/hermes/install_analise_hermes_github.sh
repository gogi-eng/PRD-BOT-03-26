#!/usr/bin/env bash
# Один раз на VPS: клон Analise_Hermes + Deploy Key (SSH push, без пароля GitHub)
set -euo pipefail
HERMES_DIR="${HERMES_GITHUB_DIR:-/root/Analise_Hermes}"
REPO_SSH="git@github.com:gogi-eng/Analise_Hermes.git"
DEPLOY_KEY="${HERMES_GITHUB_SSH_KEY:-/root/.ssh/analise_hermes_deploy}"

mkdir -p /root/.ssh
chmod 700 /root/.ssh

if [[ ! -f "$DEPLOY_KEY" ]]; then
  ssh-keygen -t ed25519 -f "$DEPLOY_KEY" -N "" -C "prd-bot-hermes-deploy"
  echo ""
  echo "=== ДОБАВЬТЕ ЭТОТ КЛЮЧ В GITHUB (Allow write access) ==="
  echo "GitHub → Analise_Hermes → Settings → Deploy keys → Add deploy key"
  echo ""
  cat "${DEPLOY_KEY}.pub"
  echo ""
  echo "==========================================================="
  echo "После добавления ключа нажмите Enter..."
  read -r
fi

if [[ ! -d "$HERMES_DIR/.git" ]]; then
  GIT_SSH_COMMAND="ssh -i ${DEPLOY_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
    git clone "$REPO_SSH" "$HERMES_DIR"
fi

git -C "$HERMES_DIR" config user.name "PRD-BOT Hermes"
git -C "$HERMES_DIR" config user.email "hermes-bot@users.noreply.github.com"
git -C "$HERMES_DIR" config core.sshCommand \
  "ssh -i ${DEPLOY_KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git -C "$HERMES_DIR" remote set-url origin "$REPO_SSH"

echo ""
echo "Проверка SSH к GitHub..."
if ssh -i "$DEPLOY_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 | grep -qi "successfully authenticated"; then
  echo "SSH OK"
else
  echo "Если видите 'Permission denied' — ключ ещё не добавлен в Deploy keys (с галочкой Write)."
fi

cat <<EOF

OK: $HERMES_DIR
Deploy key: $DEPLOY_KEY

Тест публикации:
  cd /root/AGENT-WORLD
  export HERMES_GITHUB_DIR=$HERMES_DIR
  ./venv/bin/python3 scripts/hermes_cursor_feed.py --hours 336 --git-push --force

EOF
