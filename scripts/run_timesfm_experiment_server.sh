#!/usr/bin/env bash
# TimesFM 2.5 × Bybit — прогон на сервере (Bybit API там доступен).
# Использование:
#   cd /root/AGENT-WORLD
#   bash scripts/run_timesfm_experiment_server.sh BTCUSDT
#   bash scripts/run_timesfm_experiment_server.sh ETHUSDT 30
set -euo pipefail

SYMBOL="${1:-BTCUSDT}"
MAX_SIGNALS="${2:-20}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

# timesfm 2.5 (Apache) — один раз:
# "$PY" -m pip install "timesfm[torch]==2.0.2"

OUT_DIR="${ROOT}/reports/timesfm_experiment"
mkdir -p "$OUT_DIR"

SKIPPED="${ROOT}/data/supervisor/skipped_backtest/results.jsonl"
LEDGER="${ROOT}/data/ledger/signal_ledger.jsonl"

ARGS=(
  scripts/timesfm_bybit_compare.py
  --symbol "$SYMBOL"
  --max-signals "$MAX_SIGNALS"
  --klines-bars 2000
  --out-dir "$OUT_DIR"
)

if [[ -f "$SKIPPED" ]]; then
  ARGS+=(--skipped-file "$SKIPPED")
fi
if [[ -f "$LEDGER" ]]; then
  ARGS+=(--ledger-file "$LEDGER")
fi

echo "=== TimesFM experiment: $SYMBOL (max $MAX_SIGNALS signals) ==="
"$PY" "${ARGS[@]}"
echo "=== Done. Reports in $OUT_DIR ==="
