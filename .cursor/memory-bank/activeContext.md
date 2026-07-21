# Активный контекст

Обновлено: 2026-07-21 (автоперенос текущей сессии)

## Сейчас в фокусе

1. **Memory Bank в git** — чтобы Cursor под аккаунтом пользователя читал память на **любом ПК** после `git pull`
2. Hermes **OFF**; OpenRouter оставлен (макро / Bybit AI / советы)
3. Risk: **pnl=$0 не в серию** убытков (`6fac474` / `2846bb2` на `20.07.26-*`)
4. NY-блок: `skip_weekends` + `skip_us_holidays`
5. Soft ×0.55 на прод и AW

## Итог этой длинной сессии (19–21.07)

- Разбор: суббота 18.07 «закрытие» = тихий рынок + ложный NY в выходные; balance=0 = сбой API кошелька (потом ~19 USDT снова)
- Выходные/праздники: фикс `ny_open_block`
- Wallet: retry + fallback equity
- Soft ×0.55 на прод
- Hermes полностью выкл (кнопка, bypass, `hermes.enabled: false`)
- Ветки `19.07.26-*` → `20.07.26-*` (Hermes-off + flat PnL)
- SSH с Cursor на VPS **timeout** — деплой только руками в консоли DO
- Consecutive losses: 3 = panic supervisor, 4 = AUTO-STOP risk; $0 больше не +1
- ESPORTS на AW — ещё **не** в blacklist (кандиддат)
- Zone: ON песочница / OFF прод

## Открыто

- Подтвердить на сервере: hash `20.07.26-*`, `hermes.enabled: false`, процессы hermes мертвы
- По запросу: blacklist ESPORTSUSDT на AW
- Не снимать supervisor / $10 / max_positions

## Как читать на другом ПК

1. Войти в Cursor под тем же аккаунтом (ник).
2. Вставить сниппет из `.cursor/USER-RULES-SNIPPET.txt` в **Settings → Rules → User Rules** (один раз — едет с аккаунтом).
3. `git fetch` + checkout дневной ветки `21.07.26-PRD-BOT-ALL` (или новее) — в репо уже есть `.cursor/memory-bank/` и `memory-bank.mdc`.

Hashes Memory Bank: PRD `e4f3bd5`, AW `bf9389d` (поверх `1ab59e4` / `f02a9aa`).
