# Средний приоритет — план на 31.05.2026 14:00 (UTC+3 / локальное время сервера)

## Напоминание

**Дата:** 31 мая 2026, **14:00** (проверьте часовой пояс VPS: `timedatectl` на сервере).

**Цель:** внедрить улучшения входа фазы 2 (без переноса всего SMC-движка).

## Задачи

1. **Советник входа** (`prd_agent/risk/entry_advisor.py`)
   - Блок: нет SL/TP, RR ниже порога, anti-FOMO (цена у хая/лоя 12 свечей 15m).

2. **Лимитный вход Telegram** — доработка таймаута лимит-ордера (отмена если не исполнен за N мин).

3. **Метаданные TA для супервизора плеча** — в `raw`: `rr_ratio`, `atr_pct`, `dist_to_ema_pct`, `dist_to_high_pct`.

4. **Параллельные klines** — `asyncio.Semaphore` + `gather` в `volatility_ta` (уже есть `parallel_klines`).

## Уведомления 31.05.2026 14:00

### Сервер (cron)

```bash
# Один раз на сервере:
bash /root/PRD-BOT-ALL/scripts/register_medium_priority_reminder.sh
```

### Вручную (тест)

```bash
cd /root/PRD-BOT-ALL
./venv/bin/python3 scripts/remind_medium_priority_work.py --force
```

### Cursor (ПК)

Откройте файл `.cursor/REMINDER_31_05_26_14_00.md` в проекте — там чеклист.

## После внедрения

```bash
cd /root/PRD-BOT-ALL
git pull origin 26.05.26-ALL
bash scripts/rebuild_venv.sh
sudo systemctl restart trading_bot
```
