# НАПОМИНАНИЕ — 30.06.2026

## Чекпоинт наблюдения (пакет 1+2 + Hermes bypass)

- [ ] Ветки: `30.06.26-PRD-BOT-ALL` и `30.06.26-AGENT-WORLD`
- [ ] Статистика PRD vs WORLD с **24.06.2026** (сделки, WR, PnL, holding time)
- [ ] Hermes: свежий `HERMES_LIVE.md` + логи `hermes_bypass` / без `early_breakeven`
- [ ] Проверить `signals_inbox.jsonl` и reject в telegram_signal_agent
- [ ] **Одна** правка config (ZeroOne) по матрице — или продлить наблюдение до 03.07
- [ ] Push в GitHub + деплой на сервер (если была правка)

Подробно: `.cursor/PLAN_30_06_26.md`

**На сервере (авто):**
```bash
cd /root/PRD-BOT-ALL
bash scripts/register_checkpoint_30_06_reminder.sh
# или systemd:
sudo cp deploy/checkpoint-30-06-reminder.service deploy/checkpoint-30-06-reminder.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now checkpoint-30-06-reminder.timer
```
**Тест Telegram:** `python scripts/remind_checkpoint_30_06_26.py --force`

**В чате Cursor:** «план 30.06» или «чекпоинт наблюдения»
