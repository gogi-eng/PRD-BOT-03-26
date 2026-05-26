#!/usr/bin/env bash
# Сброс дневного убытка / AUTO-STOP и (опционально) лимита сделок в config.yaml.
# Запуск на сервере: bash scripts/reset_risk_day.sh
# С лимитом 60: bash scripts/reset_risk_day.sh 60
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/venv/bin/python3"
MAX_TRADES="${1:-60}"

echo "=== Остановка trading_bot ==="
sudo systemctl stop trading_bot || true
sleep 2

echo "=== Лимит сделок в config.yaml → ${MAX_TRADES} ==="
if [[ -f config.yaml ]]; then
  "${PY}" - "${MAX_TRADES}" <<'PY'
import re, sys
from pathlib import Path
n = int(sys.argv[1])
p = Path("config.yaml")
text = p.read_text(encoding="utf-8")
if re.search(r"^\s*max_trades_per_day:\s*\d+", text, re.M):
    text, c = re.subn(
        r"(^(\s*)max_trades_per_day:\s*)\d+",
        rf"\g<1>{n}",
        text,
        count=1,
        flags=re.M,
    )
    if c:
        p.write_text(text, encoding="utf-8")
        print(f"OK: max_trades_per_day={n}")
    else:
        print("WARN: не удалось заменить max_trades_per_day")
else:
    print("WARN: max_trades_per_day не найден в config.yaml")
PY
  "${PY}" scripts/validate_config_yaml.py config.yaml 2>/dev/null || true
else
  echo "Нет config.yaml"
fi

echo "=== Сброс файлов состояния риска ==="
"${PY}" scripts/no_trades_advisor.py --root "$ROOT" --reset-loss-only --apply

echo "=== Запуск trading_bot ==="
sudo systemctl start trading_bot
sleep 2
systemctl is-active trading_bot && echo "trading_bot: active" || echo "trading_bot: FAILED"
journalctl -u trading_bot -n 12 --no-pager | tail -8
