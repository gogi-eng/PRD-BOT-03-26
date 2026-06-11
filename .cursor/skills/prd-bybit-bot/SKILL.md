---
name: prd-bybit-bot
description: PRD-BOT-ALL — unified Bybit linear perpetual bot with Telegram button control, trade journal, quality gate, OpenRouter macro. Use when editing this repo, deploying to DigitalOcean, fixing Telegram Conflict, or explaining config to a non-programmer user.
---

# PRD-BOT-ALL (Bybit + Telegram)

## Что это

Единый торговый бот: `run_unified.py` → `prd_agent/engine/orchestrator.py`.  
Источники сигналов: own agents, `reports/telegram_signals/signals_inbox.jsonl`, `whale_news`.  
Управление: **кнопки** в Telegram (`prd_agent/telegram/control_bot.py`), не команды.

**Не использовать:** CDC AI Agent, `crypto-agent-trading-main` — это другие стеки.

## Сервер

- Путь: `/root/PRD-BOT-ALL`
- **Ветка: каждый день новая** — `ДД.ММ.ГГ-OPT-ALL` (сегодня: `11.06.26-OPT-ALL`)
- Repo: `gogi-eng/PRD-BOT-03-26`
- Python: `venv/bin/python3 run_unified.py`
- systemd: `deploy/trading_bot.service`, отдельно `telegram_signal_agent`

## Деплой (после push)

```bash
cd /root/PRD-BOT-ALL
git fetch origin
git checkout 11.06.26-OPT-ALL
git reset --hard origin/11.06.26-OPT-ALL
bash scripts/install_production_config.sh
sudo systemctl restart trading_bot
sudo systemctl restart telegram_signal_agent   # если включён
```

**Правило веток:** в конце дня / перед деплоем — новая ветка `ДД.ММ.ГГ-OPT-ALL` от вчерашней, push на GitHub, на сервере `checkout` именно сегодняшней даты.

Проверка: один процесс `run_unified`, в логах `getUpdates 200 OK`.

## Telegram Conflict (409)

Причина: два процесса с одним `bot_token` (бот + signal agent с `control_panel_enabled: true`).

1. `pkill -f run_unified` — оставить один экземпляр
2. В `config.yaml`: `telegram_signal_agent.control_panel_enabled: false`
3. `bash scripts/fix_telegram_conflict.sh` при необходимости

## Секреты (.env)

| Переменная | Назначение |
|------------|------------|
| `BYBIT_API_KEY` / `BYBIT_API_SECRET` | Bybit |
| `TELEGRAM_TOKEN` | Бот кнопок |
| `TELEGRAM_CHAT_ID` | Уведомления |
| `OPENROUTER_API_KEY` | AI при `ai.provider: openrouter` |
| `FCC_AUTH_TOKEN` | Токен прокси FCC (обычно `freecc`) |

## Free Claude Code (опционально)

Единый AI-шлюз: `prd_agent/ai/llm_gateway.py` — макро, проверка TG-сигналов.

```yaml
ai:
  provider: fcc          # или openrouter на сервере
free_claude_code:
  enabled: true
  base_url: http://127.0.0.1:8082
  auth_token: freecc
```

Перед ботом на **том же хосте**: `uv run fcc-server` (папка free-claude-code-main).  
На **DigitalOcean** без FCC — `ai.provider: openrouter`.

Проверка: `./venv/bin/python3 scripts/check_llm.py`

Не коммитить `.env` и не логировать токены.

## Ключевые файлы

| Файл | Роль |
|------|------|
| `config.yaml` | Плечо, риск, quality_gate, openrouter |
| `data/trades/trade_history.jsonl` | Журнал ENTERED/CLOSED |
| `prd_agent/risk/quality_gate.py` | Фильтр перед ордером |
| `prd_agent/analysis/volatility_ta.py` | Теханализ волатильных пар |
| `scripts/ta_volatility_scan.py` | Ручной скан TA без торговли |
| `prd_agent/analysis/trade_analytics.py` | Отчёт для кнопки «📈 Статистика» |
| `prd_agent/analysis/macro_ai.py` | OpenRouter + RSS whale_news |
| `scripts/trade_analytics.py` | CLI отчёт |
| `docs/TZ_MODERNIZATION_2026.md` | ТЗ этапов 0–4 |

## Кнопки Telegram

- **📈 Статистика** — winrate/PnL по журналу за `analytics.report_hours`
- **🧠 Макро** — OpenRouter + заголовки RSS (не CDC)
- **📊 Статус** — таблица позиций и риска
- **🛑 Emergency stop** — стоп торговли

## Quality gate (перед ордером)

Проверки: confidence, SL+TP, RR ≥ 2.5, объём 24h, blacklist мем-символов.  
Отказ → `signal_skipped` в Telegram, запись в ledger.

## Диагностика

```bash
python scripts/healthcheck_agents.py
python scripts/trade_analytics.py --hours 24
python scripts/analyze_bot_log.py
journalctl -u trading_bot -n 80 --no-pager
```

## Пользователь

Инженер по промышленной безопасности, не программист. Отдавать **полные файлы** или точные команды на сервере. Объяснять по-русски, что меняется и зачем.

## Целевые параметры (ТЗ)

- `leverage: 3`, `max_positions: 2`, `min_signal_confidence: 0.68`
- `auto_apply_low_risk: false`
- Trailing: activation 0.25%, distance 0.30%
