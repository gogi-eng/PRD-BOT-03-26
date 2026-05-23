#!/bin/bash
# Обновление бота на сервере (DigitalOcean). Запуск: bash scripts/server_update.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BRANCH="${1:-21.05.26-ALL}"

echo "=== PRD-BOT-ALL: обновление на сервере ==="
echo "Папка: $ROOT"
echo "Ветка: $BRANCH"

git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

# auto_start в config.yaml (если ещё нет)
if [ -f config.yaml ] && ! grep -q 'auto_start' config.yaml; then
  echo "Добавляю trading.auto_start: true в config.yaml ..."
  python3 - <<'PY'
from pathlib import Path
import yaml
p = Path("config.yaml")
data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
t = data.setdefault("trading", {})
t["auto_start"] = True
p.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
PY
fi

if [ -x venv/bin/python3 ]; then
  PY=venv/bin/python3
  PIP=venv/bin/pip
elif [ -x .venv/bin/python3 ]; then
  PY=.venv/bin/python3
  PIP=.venv/bin/pip
else
  echo "Нет venv — создайте: bash scripts/rebuild_venv.sh"
  exit 1
fi

echo "Python: $($PY --version)"

echo ""
echo "=== Зависимости (unified + telethon) ==="
"$PIP" install -q -r requirements-unified.txt

echo ""
echo "=== Healthcheck ==="
"$PY" scripts/healthcheck_agents.py

echo ""
echo "=== Диагностика (без ордеров) ==="
"$PY" scripts/diagnose_trading.py || true

echo ""
echo "=== Перезапуск службы ==="
SERVICE=""
for name in prd-unified trading_bot trading-bot prd_bot run_unified scalp_bot; do
  if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${name}.service"; then
    SERVICE="$name"
    break
  fi
done
if [ -z "$SERVICE" ]; then
  SERVICE=$(systemctl list-units --type=service --all 2>/dev/null | grep -oiE '[^ ]*bot[^ ]*\.service' | head -1 | sed 's/\.service//')
fi
if [ -n "$SERVICE" ]; then
  sudo systemctl restart "$SERVICE"
  sleep 2
  sudo systemctl status "$SERVICE" --no-pager | head -20
  echo "Лог: journalctl -u $SERVICE -n 40 --no-pager"
else
  echo "Служба systemd не найдена. Остановите старый процесс и запустите:"
  echo "  pkill -f run_unified.py || true"
  echo "  nohup python3 run_unified.py >> bot.log 2>&1 &"
fi

if systemctl list-unit-files --type=service 2>/dev/null | grep -q '^telegram_signal_agent.service'; then
  echo ""
  echo "=== Перезапуск telegram_signal_agent ==="
  sudo systemctl restart telegram_signal_agent
  sleep 2
  sudo systemctl status telegram_signal_agent --no-pager | head -15 || true
fi

echo ""
echo "=== Готово ==="
