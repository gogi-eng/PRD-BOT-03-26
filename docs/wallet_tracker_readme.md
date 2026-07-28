# Wallet Tracker (v1) — как включить и пользоваться

Агент смотрит за выбранными кошельками (ETH), собирает крупные свапы/токен-трансферы и даёт **советы** торговому боту (long/short/neutral).  
**В v1 ордера сам не ставит.**

## Что такое `watches` (простыми словами)

`watches` — это **список наблюдения**: номера кошельков в сети Ethereum, за которыми агент следит.

Аналогия: вы записали в блокнот «смотреть за этими людьми на рынке».  
Когда один из них купил или продал крупно токен, который есть на Bybit (например PEPE → `1000PEPEUSDT`), агент может прислать **совет** в Telegram. Это не приказ и не автосделка.

- Адрес = длинный код вида `0x` + 40 символов (цифры и буквы a–f).
- Заглушка `0x...` агент **специально игнорирует** — чтобы не слать мусор.
- В песочнице уже лежат **публичные** известные адреса (Vitalik, Curve founder, Wintermute, Jump, DWF, a16z, Amber) с комментарием `# source:` в yaml.

## Где в config

Песочница (`deploy/config.agent_world_sandbox.yaml`):

```yaml
wallet_tracker:
  enabled: true
  telegram_notify: true
  poll_interval_sec: 300
  min_swap_usd: 5000
  recommendation_ttl_sec: 3600
  symbol_cooldown_sec: 1800
  chains: ["eth"]
  watches:
    - address: "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
      label: "vitalik_buterin"
      chain: eth
```

Прод: `enabled: false`, `telegram_notify: false` (код и список watches есть, цикл не крутится).  
При `telegram_notify: true` новая рекомендация LONG/SHORT уходит в Telegram через notifier (без новой кнопки панели).

## Ключи в `.env` (не в yaml и не в git)

Нужен **хотя бы один**:

| Переменная | Зачем |
|------------|--------|
| `DEBANK_ACCESS_KEY` | Debank OpenAPI (предпочтительно, есть USD в истории) |
| `ETHERSCAN_API_KEY` | Etherscan `tokentx` + оценка USD через CoinGecko |
| `DUNE_API_KEY` | Зарезервирован; в v1 SQL-провайдер ещё не подключён |

Если ключей нет — в логе: `Wallet tracker disabled: no API key`. Бот не падает.

## Как добавить свой кошелёк

1. Откройте на Etherscan страницу кошелька (или скопируйте адрес из надёжной статьи Arkham / Lookonchain с **полным** `0x…`).
2. В `deploy/config.agent_world_sandbox.yaml` → `wallet_tracker.watches` добавьте блок:
   ```yaml
   - address: "0xВашПолныйАдрес40символов"
     label: "понятное_имя"
     chain: eth
     # source: ссылка откуда взяли
   ```
3. Не вставляйте укороченные адреса вида `0x25C…66E2a` — только полный hex.
4. Placeholder `0x...` агент игнорирует.
5. Деплой / `install_*_config` + рестарт `trading_bot_agent_world`.

## Что смотреть в логе

```text
Wallet tracker enabled
Wallet tracker advisory
Wallet flow recommendation 1000PEPEUSDT bias=long ...
Wallet tracker telegram sent 1000PEPEUSDT bias=long ...
Wallet tracker telegram skip dedup ...
Wallet tracker soft match ...
Wallet tracker disabled: no API key
```

Файл рекомендаций (на сервере, в gitignore): `data/wallet_tracker/recommendations.jsonl`.

Отчёт текстом (без кнопки Telegram): `orch.get_wallet_tracker_report()`.

## Важно

- Маппинг `PEPE` → `1000PEPEUSDT` и т.п.; стейблы и trash без Bybit-маппинга отбрасываются.
- Не копируйте слепо мемы с DEX — только если контракт торгуется как Bybit linear USDT perpetual.
- **Прошлые сделки китов ≠ будущая прибыль.** Сообщения — советы, не гарантия.
- Маркет-мейкеры (Wintermute, Jump, DWF, Amber) часто делают много служебных переводов — шума может быть больше, чем у «обычного» кита.
