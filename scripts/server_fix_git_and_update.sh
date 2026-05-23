#!/bin/bash
# Если git pull/checkout падает — этот скрипт выравнивает сервер под GitHub.
# Сохраняет .env и config.yaml, затем: reset на origin/19.05.26-ALL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BRANCH="${1:-23.05.26-ALL}"
BACKUP="$HOME/PRD-BOT-ALL-backup-$(date +%Y%m%d_%H%M%S)"

echo "=== Резервная копия настроек ==="
mkdir -p "$BACKUP"
for f in .env config.yaml telegram_user_signal_agent.session telegram_signal_agent_state.json; do
  if [ -e "$f" ]; then
    cp -a "$f" "$BACKUP/" 2>/dev/null || true
    echo "  сохранён: $f -> $BACKUP/"
  fi
done
if [ -f reports/telegram_signals/signals_inbox.jsonl ]; then
  mkdir -p "$BACKUP/reports/telegram_signals"
  cp -a reports/telegram_signals/signals_inbox.jsonl "$BACKUP/reports/telegram_signals/"
fi

echo "=== Git: выравнивание с origin/$BRANCH ==="
git fetch origin
git checkout -f "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH"
git reset --hard "origin/$BRANCH"
git clean -fd -e .env -e config.yaml -e venv -e .venv -e data -e bot.log \
  -e telegram_user_signal_agent.session -e telegram_signal_agent_state.json -e reports

echo "=== Восстановление .env и config.yaml ==="
[ -f "$BACKUP/.env" ] && cp -a "$BACKUP/.env" .env && echo "  .env восстановлен"
[ -f "$BACKUP/config.yaml" ] && cp -a "$BACKUP/config.yaml" config.yaml && echo "  config.yaml восстановлен"
[ -f "$BACKUP/telegram_user_signal_agent.session" ] && cp -a "$BACKUP/telegram_user_signal_agent.session" . && echo "  session восстановлен"
[ -f "$BACKUP/telegram_signal_agent_state.json" ] && cp -a "$BACKUP/telegram_signal_agent_state.json" . && echo "  state.json восстановлен"
if [ -f "$BACKUP/reports/telegram_signals/signals_inbox.jsonl" ]; then
  mkdir -p reports/telegram_signals
  cp -a "$BACKUP/reports/telegram_signals/signals_inbox.jsonl" reports/telegram_signals/
fi
mkdir -p reports/telegram_signals data/trades
touch reports/telegram_signals/signals_inbox.jsonl

# auto_start
if [ -f config.yaml ] && ! grep -q 'auto_start' config.yaml; then
  python3 - <<'PY'
from pathlib import Path
import yaml
p = Path("config.yaml")
data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
data.setdefault("trading", {})["auto_start"] = True
p.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
print("  добавлен trading.auto_start: true")
PY
fi

bash scripts/rebuild_venv.sh
PY=./venv/bin/python3

echo ""
echo "=== Healthcheck ==="
"$PY" scripts/healthcheck_agents.py || true

echo ""
echo "=== no_trades_advisor (проверка + рекомендации) ==="
"$PY" scripts/no_trades_advisor.py --hours 6 --hours 24 --telegram || true

echo ""
echo "=== Применить ослабление порогов и сброс риск-стопа? ==="
echo "Запустите вручную:"
echo "  python3 scripts/no_trades_advisor.py --apply --reset-loss --telegram"
echo ""
echo "=== Диагностика торговли ==="
"$PY" scripts/diagnose_trading.py || true

echo ""
echo "=== Перезапуск бота ==="
SERVICE=""
for name in prd-unified trading_bot trading-bot prd_bot run_unified scalp_bot; do
  if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${name}.service"; then
    SERVICE="$name"
    break
  fi
done
if [ -n "$SERVICE" ]; then
  sudo systemctl restart "$SERVICE"
  sleep 2
  sudo systemctl status "$SERVICE" --no-pager | head -15
else
  pkill -f run_unified.py 2>/dev/null || true
  nohup python3 run_unified.py >> bot.log 2>&1 &
  echo "Запущен run_unified.py в фоне, лог: tail -f bot.log"
fi

echo ""
echo "Готово. Резервная копия: $BACKUP"
