# AGENT-WORLD — прогон ALGO на субаккаунте

Отдельная папка на сервере: **`/root/AGENT-WORLD`**.  
Основной прод: **`/root/PRD-BOT-ALL`** — не трогаем.

## Зачем

- Тестировать ветку **`12.06.26-ALGO`** без риска для основного счёта.
- Ключи Bybit — **субаккаунт** (отдельная API-пара в Bybit → Sub-account).
- Кнопки Telegram **отключены** (`control_polling_enabled: false`) — нет Conflict с основным ботом.
- Уведомления в Telegram **работают** (тот же chat_id).

## 1. Ключи субаккаунта в Bybit

1. Bybit → API → создать ключ для **Sub-account** (не main).
2. Права: Read + Trade (без Withdraw).
3. Записать в `/root/AGENT-WORLD/.env`:

```env
BYBIT_SUB_API_KEY=ваш_ключ_субаккаунта
BYBIT_SUB_API_SECRET=ваш_секрет_субаккаунта
TELEGRAM_TOKEN=... 
TELEGRAM_CHAT_ID=...
```

(Можно и `BYBIT_API_KEY` / `BYBIT_API_SECRET` — если только субаккаунт в этой папке.)

## 2. Деплой одной командой (на сервере)

```bash
cd /root/AGENT-WORLD 2>/dev/null || true
sudo bash -c 'curl -fsSL https://raw.githubusercontent.com/gogi-eng/PRD-BOT-03-26/12.06.26-ALGO/scripts/deploy_agent_world_algo.sh | bash'
```

Или если репозиторий уже есть:

```bash
cd /root/AGENT-WORLD
git fetch origin 12.06.26-ALGO
git reset --hard origin/12.06.26-ALGO
sudo bash scripts/deploy_agent_world_algo.sh /root/AGENT-WORLD 12.06.26-ALGO
```

## 3. Проверка

```bash
sudo systemctl status trading_bot_agent_world --no-pager
tail -30 /root/AGENT-WORLD/bot.log
grep -E "API cycle|retest_watch|entry_pipeline|StrategyRouter" /root/AGENT-WORLD/bot.log | tail -20
/root/AGENT-WORLD/venv/bin/python3 /root/AGENT-WORLD/scripts/algo_skip_baseline.py --hours 24
```

Ожидаемо в логах:
- `API cycle N: X REST calls`
- `retest_watch: ... WAIT → CONFIRMED` (когда будет сигнал)
- `StrategyRouter: swing` или `scalp`

## 4. Остановка песочницы

```bash
sudo systemctl stop trading_bot_agent_world
```

Основной `trading_bot` (PRD-BOT-ALL) продолжает работать.

## Отличия sandbox-config

| Параметр | AGENT-WORLD | PRD-BOT-ALL (прод) |
|----------|-------------|---------------------|
| max_positions | 2 | 6 |
| risk_pct | 0.15% | 0.35% |
| leverage | 10 | 20 |
| Telegram кнопки | выкл | вкл |
| signal_agent | выкл | вкл |
| ветка | 12.06.26-ALGO | OPT-ALL |
