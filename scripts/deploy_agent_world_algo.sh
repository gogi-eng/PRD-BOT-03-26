#!/usr/bin/env bash
# Деплой ветки ALGO в /root/AGENT-WORLD для прогона на субаккаунте.
# Использование на сервере:
#   sudo bash scripts/deploy_agent_world_algo.sh
#   sudo bash scripts/deploy_agent_world_algo.sh /root/AGENT-WORLD 12.06.26-ALGO
set -euo pipefail

REPO_DIR="${1:-/root/AGENT-WORLD}"
BRANCH="${2:-12.06.26-ALGO}"
REMOTE="${3:-https://github.com/gogi-eng/PRD-BOT-03-26.git}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "Клонируем репозиторий в $REPO_DIR (ветка $BRANCH) ..."
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone -b "$BRANCH" "$REMOTE" "$REPO_DIR"
fi

cd "$REPO_DIR"

if [[ -f config.yaml ]]; then
  cp config.yaml "/root/config.agent_world.bak.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
fi

git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REMOTE"
git fetch origin "$BRANCH"
REF="origin/$BRANCH"
if ! git rev-parse --verify "$REF" >/dev/null 2>&1; then
  echo "⚠️  $REF не найден — используем FETCH_HEAD (после git fetch origin $BRANCH)"
  REF="FETCH_HEAD"
fi
git checkout -B "$BRANCH" "$REF"
git reset --hard "$REF"
echo "✓ Код: $(git rev-parse --short HEAD) ветка $(git branch --show-current)"

PYTHON=""
for cand in venv/bin/python3 venv/bin/python .venv/bin/python3; do
  if [[ -x "$REPO_DIR/$cand" ]]; then
    PYTHON="$REPO_DIR/$cand"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "Создаём venv ..."
  python3 -m venv venv
  PYTHON="$REPO_DIR/venv/bin/python3"
  "$PYTHON" -m pip install -U pip wheel
  if [[ -f backend/requirements.txt ]]; then
    "$PYTHON" -m pip install -r backend/requirements.txt
  fi
fi

bash scripts/install_agent_world_config.sh

mkdir -p reports/world reports/telegram_signals data/ledger data/trades

if [[ ! -f .env ]]; then
  echo ""
  echo "⚠️  Создайте $REPO_DIR/.env с ключами СУБАККАУНТА:"
  echo "    BYBIT_API_KEY=..."
  echo "    BYBIT_API_SECRET=..."
  echo "    TELEGRAM_TOKEN=... (уведомления, кнопки отключены в sandbox)"
  echo "    TELEGRAM_CHAT_ID=..."
  echo ""
fi

if [[ -f deploy/trading_bot_agent_world.service ]]; then
  cp deploy/trading_bot_agent_world.service /etc/systemd/system/trading_bot_agent_world.service
  systemctl daemon-reload
  systemctl enable trading_bot_agent_world
  systemctl restart trading_bot_agent_world
  echo ""
  echo "=== trading_bot_agent_world ==="
  systemctl is-active trading_bot_agent_world || true
  journalctl -u trading_bot_agent_world -n 15 --no-pager || true
else
  echo "Нет deploy/trading_bot_agent_world.service — запуск вручную:"
  echo "  cd $REPO_DIR && ./venv/bin/python3 run_unified.py"
fi

echo ""
echo "Готово. Лог: $REPO_DIR/bot.log"
echo "Baseline SKIP: $PYTHON scripts/algo_skip_baseline.py --hours 168"
