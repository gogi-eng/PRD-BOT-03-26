#!/usr/bin/env bash
# Обновление PRD-BOT-ALL на сервере (жёсткий reset config из git + cron + рестарт).
# Использование:
#   sudo bash scripts/server_pull_deploy.sh
#   sudo bash scripts/server_pull_deploy.sh /root/PRD-BOT-ALL 07.06.26-OPT-ALL

set -euo pipefail

REPO_DIR="${1:-/root/PRD-BOT-ALL}"
BRANCH="${2:-07.06.26-OPT-ALL}"

cd "$REPO_DIR"

if [[ -f config.yaml ]]; then
  cp config.yaml "/root/config.yaml.bak.$(date +%Y%m%d_%H%M%S)"
  echo "✓ Бэкап config.yaml → /root/config.yaml.bak.*"
fi

git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "✓ Код: $(git rev-parse --short HEAD) ветка $(git branch --show-current)"

PYTHON=""
for cand in venv/bin/python3 venv/bin/python .venv/bin/python3; do
  if [[ -x "$REPO_DIR/$cand" ]]; then
    PYTHON="$REPO_DIR/$cand"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "error: не найден venv/bin/python3" >&2
  exit 1
fi

mkdir -p reports/world reports/telegram_signals

if [[ -f scripts/install_agent_world_cron.sh ]]; then
  bash scripts/install_agent_world_cron.sh --repo-dir "$REPO_DIR" --every 10
fi

if [[ -f scripts/reset_agent_runtime_controls.py ]]; then
  "$PYTHON" scripts/reset_agent_runtime_controls.py || true
fi

if [[ -f scripts/agent_world.py ]]; then
  "$PYTHON" scripts/agent_world.py || true
fi

systemctl restart telegram_signal_agent
systemctl restart trading_bot

echo ""
echo "=== Статус ==="
systemctl is-active telegram_signal_agent trading_bot
grep -E "agent_world|market_scanner|run_loop" config.yaml | head -12
journalctl -u telegram_signal_agent -n 3 --no-pager | grep -E "started|agent_world" || true
