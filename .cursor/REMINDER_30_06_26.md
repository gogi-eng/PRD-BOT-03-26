# НАПОМИНАНИЕ — 30.06.2026 (полный день)

## A. 10:00 — Торговля
- [ ] Ветки `30.06.26-PRD-BOT-ALL` + `30.06.26-AGENT-WORLD`
- [ ] Статистика PRD vs WORLD с **24.06** (WR, PnL, holding time)
- [ ] Hermes + логи + signals_inbox
- [ ] **Одна** правка config (ZeroOne) или продлить до 03.07

## B. 12:00–15:00 — Инфраструктура (без смены стратегии)
- [ ] GitHub Actions: pytest smoke
- [ ] Тесты `prd_agent/risk/guard.py`
- [ ] `docs/DEPLOY.md`
- [ ] `scripts/backup_bot_data.sh` + cron

## C. 16:00 — Деплой (только если меняли config в A)
- [ ] PRD + WORLD: fetch, install_config, restart

## D. 18:00 — Фиксация
- [ ] Дамп PRD 30.06.26 → push
- [ ] Итог в этот файл (таблица + решение)

Подробно: `.cursor/PLAN_30_06_26.md`

**Авто:** Telegram 10:00 — `remind_checkpoint_30_06_26.py`  
**Сервер:** `bash scripts/register_checkpoint_30_06_reminder.sh`  
**Тест:** `python scripts/remind_checkpoint_30_06_26.py --force`  
**Cursor:** «план 30.06»
