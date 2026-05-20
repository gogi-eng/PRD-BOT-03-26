#!/bin/bash
# Если git pull/checkout падает — этот скрипт выравнивает сервер под GitHub.
# Сохраняет .env и config.yaml, затем: reset на origin/19.05.26-ALL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BRANCH="${1:-19.05.26-ALL}"
BACKUP="$HOME/PRD-BOT-ALL-backup-$(date +%Y%m%d_%H%M%S)"

echo "=== Резервная копия настроек ==="
mkdir -p "$BACKUP"
for f in .env config.yaml run_unified.py scripts/run_no_trades_check.sh; do
  if [ -e "$f" ]; then
    cp -a "$f" "$BACKUP/" 2>/dev/null || true
    echo "  сохранён: $f -> $BACKUP/"
  fi
done

echo "=== Git: выравнивание с origin/$BRANCH ==="
git fetch origin
git checkout -f "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH"
git reset --hard "origin/$BRANCH"
git clean -fd -e .env -e config.yaml -e venv -e .venv -e data -e bot.log

echo "=== Восстановление .env и config.yaml ==="
[ -f "$BACKUP/.env" ] && cp -a "$BACKUP/.env" .env && echo "  .env восстановлен"
[ -f "$BACKUP/config.yaml" ] && cp -a "$BACKUP/config.yaml" config.yaml && echo "  config.yaml восстановлен"

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

if [ -d venv ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
elif [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "Создайте venv: python3 -m venv venv && source venv/bin/activate && pip install -r requirements-unified.txt"
  exit 1
fi

echo ""
echo "=== no_trades_advisor (проверка + рекомендации) ==="
python3 scripts/no_trades_advisor.py --hours 6 --hours 24 --telegram || true

echo ""
echo "=== Применить ослабление порогов и сброс риск-стопа? ==="
echo "Запустите вручную:"
echo "  python3 scripts/no_trades_advisor.py --apply --reset-loss --telegram"
echo ""
echo "=== Диагностика торговли ==="
python3 scripts/diagnose_trading.py || true

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
