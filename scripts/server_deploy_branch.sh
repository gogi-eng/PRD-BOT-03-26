#!/bin/bash
# Деплой ветки на сервере (сохраняет .env, config.yaml, сессию Telegram).
# Запуск: cd /root/PRD-BOT-ALL && bash scripts/server_deploy_branch.sh 23.05.26-ALL
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BRANCH="${1:-23.05.26-ALL}"
BACKUP="$HOME/PRD-BOT-ALL-backup-$(date +%Y%m%d_%H%M%S)"

echo "=== Резервная копия (настройки + Telegram) ==="
mkdir -p "$BACKUP"
for f in .env config.yaml telegram_user_signal_agent.session telegram_signal_agent_state.json; do
  if [ -e "$f" ]; then
    cp -a "$f" "$BACKUP/"
    echo "  $f"
  fi
done
if [ -f reports/telegram_signals/signals_inbox.jsonl ]; then
  mkdir -p "$BACKUP/reports/telegram_signals"
  cp -a reports/telegram_signals/signals_inbox.jsonl "$BACKUP/reports/telegram_signals/"
  echo "  reports/telegram_signals/signals_inbox.jsonl"
fi

echo ""
echo "=== Git → origin/$BRANCH ==="
git fetch origin
git checkout -f "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH" "origin/$BRANCH"
git reset --hard "origin/$BRANCH"
git clean -fd \
  -e .env -e config.yaml \
  -e venv -e .venv \
  -e data -e bot.log \
  -e telegram_user_signal_agent.session \
  -e telegram_signal_agent_state.json \
  -e reports

echo ""
echo "=== Восстановление из резервной копии ==="
[ -f "$BACKUP/.env" ] && cp -a "$BACKUP/.env" .env && echo "  .env"
[ -f "$BACKUP/config.yaml" ] && cp -a "$BACKUP/config.yaml" config.yaml && echo "  config.yaml"
[ -f "$BACKUP/telegram_user_signal_agent.session" ] && cp -a "$BACKUP/telegram_user_signal_agent.session" . && echo "  session"
[ -f "$BACKUP/telegram_signal_agent_state.json" ] && cp -a "$BACKUP/telegram_signal_agent_state.json" . && echo "  state.json"
if [ -f "$BACKUP/reports/telegram_signals/signals_inbox.jsonl" ]; then
  mkdir -p reports/telegram_signals
  cp -a "$BACKUP/reports/telegram_signals/signals_inbox.jsonl" reports/telegram_signals/
  echo "  inbox.jsonl"
fi

mkdir -p reports/telegram_signals data/trades
touch reports/telegram_signals/signals_inbox.jsonl

echo ""
echo "=== venv ==="
bash scripts/rebuild_venv.sh

echo ""
echo "=== Healthcheck ==="
./venv/bin/python3 scripts/healthcheck_agents.py

if [ -f deploy/telegram_signal_agent.service ]; then
  sudo cp deploy/telegram_signal_agent.service /etc/systemd/system/
  sudo systemctl daemon-reload
fi

echo ""
echo "=== Перезапуск служб ==="
for svc in trading_bot telegram_signal_agent; do
  if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${svc}.service"; then
    sudo systemctl restart "$svc"
    sleep 2
    sudo systemctl status "$svc" --no-pager | head -10 || true
    echo ""
  fi
done

echo "=== Готово. Резервная копия: $BACKUP ==="
echo "Лог бота: tail -30 $ROOT/bot.log"
