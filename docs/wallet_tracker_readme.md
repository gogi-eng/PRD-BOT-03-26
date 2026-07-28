# Wallet Tracker (v1) — как включить и пользоваться

Агент смотрит за выбранными кошельками (ETH), собирает крупные свапы/токен-трансферы и даёт **советы** торговому боту (long/short/neutral).  
**В v1 ордера сам не ставит.**

## Где в config

Песочница (`deploy/config.agent_world_sandbox.yaml`):

```yaml
wallet_tracker:
  enabled: true
  poll_interval_sec: 300
  min_swap_usd: 5000
  recommendation_ttl_sec: 3600
  symbol_cooldown_sec: 1800
  chains: ["eth"]
  watches:
    - address: "0xРеальныйАдресКошелька"
      label: "whale_1"
      chain: eth
```

Прод: `enabled: false` (код есть, цикл не крутится).

## Ключи в `.env` (не в yaml и не в git)

Нужен **хотя бы один**:

| Переменная | Зачем |
|------------|--------|
| `DEBANK_ACCESS_KEY` | Debank OpenAPI (предпочтительно, есть USD в истории) |
| `ETHERSCAN_API_KEY` | Etherscan `tokentx` + оценка USD через CoinGecko |
| `DUNE_API_KEY` | Зарезервирован; в v1 SQL-провайдер ещё не подключён |

Если ключей нет — в логе: `Wallet tracker disabled: no API key`. Бот не падает.

## Как добавить кошелёк

1. Откройте `deploy/config.agent_world_sandbox.yaml` (или live `config.yaml` на сервере после install).
2. В `wallet_tracker.watches` добавьте адрес `0x...` и понятный `label`.
3. Placeholder вида `0x...` агент **игнорирует** (чтобы случайно не слать мусор).
4. Деплой / `install_*_config` + рестарт `trading_bot_agent_world`.

## Что смотреть в логе

```text
Wallet tracker enabled
Wallet tracker advisory
Wallet flow recommendation 1000PEPEUSDT bias=long ...
Wallet tracker soft match ...
Wallet tracker disabled: no API key
```

Файл рекомендаций (на сервере, в gitignore): `data/wallet_tracker/recommendations.jsonl`.

Отчёт текстом (без кнопки Telegram): `orch.get_wallet_tracker_report()`.

## Важно

- Маппинг `PEPE` → `1000PEPEUSDT` и т.п.; стейблы и trash без Bybit-маппинга отбрасываются.
- Не копируйте слепо мемы с DEX — только если контракт торгуется как Bybit linear USDT perpetual.
