#!/usr/bin/env bash
# Установка/обновление systemd-службы ai4trade → Telegram
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/scripts/systemd/ai4trade_telegram_notify.service.example"
UNIT_DST="/etc/systemd/system/ai4trade_telegram_notify.service"

echo "==> PRD-BOT-ALL: $ROOT"
if [[ ! -f "$ROOT/.env" ]]; then
  echo "WARN: нет $ROOT/.env — нужны TELEGRAM_TOKEN и TELEGRAM_CHAT_ID"
fi

echo "==> git pull (ветка 30.05.26-OPT-ALL)"
cd "$ROOT"
git fetch origin 30.05.26-OPT-ALL
git checkout 30.05.26-OPT-ALL
git pull origin 30.05.26-OPT-ALL

echo "==> копируем unit + EnvironmentFile .env"
cp "$UNIT_SRC" "$UNIT_DST"
sed -i "s|/root/PRD-BOT-ALL|$ROOT|g" "$UNIT_DST"

echo "==> проверка Telegram credentials"
python3 -c "
from pathlib import Path
from prd_agent.integrations.telegram_credentials import resolve_telegram
t,c=resolve_telegram({}, root=Path('$ROOT'))
assert t and c, 'TELEGRAM_TOKEN или TELEGRAM_CHAT_ID не найдены в .env'
print('OK: token и chat_id загружены')
"

systemctl daemon-reload
systemctl enable ai4trade_telegram_notify
systemctl restart ai4trade_telegram_notify
sleep 2
systemctl --no-pager status ai4trade_telegram_notify || true
echo "==> лог: journalctl -u ai4trade_telegram_notify -f"
