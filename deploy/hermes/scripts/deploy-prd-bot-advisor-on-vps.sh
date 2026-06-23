#!/usr/bin/env bash
# Создаёт skills/scripts/prompts на VPS (если копировали только с ПК частично).
# Запуск на сервере: bash deploy-prd-bot-advisor-on-vps.sh
set -euo pipefail

H="${HOME}/.hermes"
mkdir -p "$H/scripts" "$H/skills/prd-bot-morning-brief" "$H/skills/prd-bot-skip-analysis" \
  "$H/skills/prd-bot-reflection-loop" "$H/prompts" "$H/state/history"

# --- strategy-goals.yaml ---
cat > "$H/strategy-goals.yaml" <<'EOF'
version: 1
timezone: Europe/Moscow
targets:
  return_30d_pct: 5.0
  max_drawdown_pct: 8.0
  min_sharpe: 0.8
worker:
  prod_path: /root/PRD-BOT-ALL
  sandbox_path: /root/AGENT-WORLD
  ledger_glob: data/ledger/*.jsonl
  skip_stats: data/supervisor/skip_stats.json
reflection:
  every_n_closed_trades: 10
  one_variable_only: true
  allowed_variables:
    - market_scanner_execute_min_score
    - trailing_stop.activation_pct
    - trailing_stop.distance_pct
    - execution_sr_zones.target_initial_tp_rr
    - supervisor.defensive_threshold
    - derivatives_entry_guard.enabled
  history_dir: ~/.hermes/state/history
mode: advisor_only
EOF

# --- skills ---
cat > "$H/skills/prd-bot-morning-brief/SKILL.md" <<'EOF'
---
name: prd-bot-morning-brief
description: Утренний брифинг PRD-BOT (прод + AGENT-WORLD). Только чтение.
---
# Утренний брифинг PRD-BOT
1. Europe/Moscow (UTC+3).
2. tail -n 120 /root/PRD-BOT-ALL/bot.log; tail -n 80 /root/AGENT-WORLD/bot.log (если есть).
3. skip_stats.json прод и песочница.
4. Ответ на русском: Прод | Песочница | Рекомендация (без ордеров).
Запрещено: ордера, правка config.
EOF

cat > "$H/skills/prd-bot-skip-analysis/SKILL.md" <<'EOF'
---
name: prd-bot-skip-analysis
description: Почему PRD-BOT не входит в сделки. Только анализ.
---
# Анализ skip
Источники: bot.log (grep Skip/reject), skip_stats.json, AGENT-WORLD.
Формат: главный «убийца» сигналов | supervisor | что нажать в Telegram бота.
Запрещено: торговать, менять config, отключать polling.
EOF

cat > "$H/skills/prd-bot-reflection-loop/SKILL.md" <<'EOF'
---
name: prd-bot-reflection-loop
description: ZeroOne reflection — одна гипотеза за цикл, без авто-правок config.
---
# Reflection loop
Читай ~/.hermes/strategy-goals.yaml. Ledger + skip_stats. Одна переменная из allowed_variables.
Запись гипотезы в ~/.hermes/state/history/YYYY-MM-DD-hypothesis.md.
Запрещено: менять config.yaml бота, ордера.
EOF

# --- briefing handoff ---
cat > "$H/prompts/hermes-briefing-handoff.ru.txt" <<'EOF'
Прими роль мозга для worker PRD-BOT-ALL (прод /root/PRD-BOT-ALL, песочница /root/AGENT-WORLD).
Ты только советник: логи, skip, брифинги. НЕ ставишь ордера, НЕ меняешь config, НЕ делаешь systemctl restart/pkill для trading_bot, telegram_signal_agent, prd-unified, AGENT-WORLD.
Цели: ~/.hermes/strategy-goals.yaml (+5%/30d, DD 8%).
Skills: prd-bot-morning-brief, prd-bot-skip-analysis, prd-bot-reflection-loop.
Настрой cron: будни 08:30 MSK — morning-brief; воскресенье 20:00 MSK — reflection-loop. Доставка в этот чат.
Подтверди одним предложением и жди.
EOF

# --- apply script ---
cat > "$H/scripts/apply-trading-agent-setup.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/.hermes"
mkdir -p state/history
hermes --version
hermes gateway install 2>/dev/null || true
loginctl enable-linger "$(whoami)" 2>/dev/null || true
hermes gateway start 2>/dev/null || systemctl --user start hermes-gateway 2>/dev/null || true
hermes gateway status 2>/dev/null || systemctl --user status hermes-gateway --no-pager -l || true
echo "OK. Текст для Telegram: cat ~/.hermes/prompts/hermes-briefing-handoff.ru.txt"
EOF
chmod +x "$H/scripts/apply-trading-agent-setup.sh"

# --- SOUL append (не затираем существующий полностью) ---
if ! grep -q "prd-unified" "$H/SOUL.md" 2>/dev/null; then
  cat >> "$H/SOUL.md" <<'EOF'

## Жёсткий запрет (PRD-BOT на этом сервере)
- НИКОГДА: systemctl restart/stop trading_bot, telegram_signal_agent, prd-unified, trading_bot_agent_world, telegram_signal_agent_world
- НИКОГДА: pkill по процессам PRD-BOT
- Только чтение: tail, cat, grep логов и data/
- Если нужен рестарт сервиса — скажи пользователю, он сделает сам
EOF
fi

echo "==> Файлы созданы в $H"
ls -la "$H/scripts/" "$H/skills/" "$H/prompts/" 2>/dev/null || true
echo ""
echo "Дальше:"
echo "  bash $H/scripts/apply-trading-agent-setup.sh"
echo "  cat $H/prompts/hermes-briefing-handoff.ru.txt   # вставить в Telegram Hermes"
echo ""
echo "ВАЖНО: /prd-bot-morning-brief — это сообщение БОТУ в Telegram, не команда bash!"
