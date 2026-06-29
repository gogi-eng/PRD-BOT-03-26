#!/usr/bin/env bash
# Удаление мусора из индекса git (дампы, логи, сессии, чужой стек).
set -euo pipefail
ROOT="${1:-.}"
cd "$ROOT"

git rm -rf --ignore-unmatch \
  BOT_DUMP.txt \
  BOT_DUMP_*.txt \
  bot.log.ALL.txt \
  backtest_out.json \
  backtest_quick_btc.json \
  backtest_quick_whitelist.json \
  crypto-agent-trading-main \
  telegram_user_signal_agent.session \
  telegram_signal_agent_state.json \
  reports/05.05.26 \
  reports/trade_context \
  reports/trade_history_last_24h_20260421_153334.csv \
  reports/trade_history_last_24h_20260421_153334.json \
  reports/telegram_signals/signals_inbox.jsonl \
  docs/presentation/technical_analysis_presentation_new.pptx \
  2>/dev/null || true

echo "cleanup done in $ROOT"
