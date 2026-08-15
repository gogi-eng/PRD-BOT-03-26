## 15.08.2026 — GARCH регулирует трейлинг-SL

- ТЗ: в спокойствии поджимать прибыль ближе, в шторме дать воздух (шире distance).
- Безопасный вариант: множители к уже существующему `distance_factor` (не переписывать trailing).
- AW сначала ON; прод OFF до проверки. Общий код → обе ветки дня; деплой только AW.

## 15.08.2026 — AIAI.BY вместо DeepSeek на песочнице

- Пользователь оплатил API на aiai.by; DeepSeek ранее дал 402 (нулевой баланс).
- Решение: тот же OpenAI-compatible HTTP client, base_url `https://api.aiai.by/v1`, ключ только в .env.
- Включить `ai.provider: aiai` только на AGENT-WORLD; прод masked → openrouter, без деплоя.
- Не просить ключ в чат; после деплоя smoke «ключ отсутствует» ок до ручной вставки.

## 11.08.2026 — откат manage_sl_tp_manual (как ~09.08)

- Пользователь: «верни назад как было: для всех сделок»; недоволен самостоятельной правкой.
- Откат коммитов 8f940c (PRD) / e283f9b (AW): убраны manage_sl_tp_manual: false, skip trailing/BE+ для manual, sync-overwrite SL/TP из биржи, отказ clear SL/TP в bybit_client, тест no_overwrite.
- Снова: steward двигает SL/TP (trailing/BE+/adaptive) для **всех** позиций, включая подхваченные; pply_to_manual: true.
- Остаётся без отката: manual_auto_close: false / Companion не закрывает manual time-stop (hotfix 08.08).
- Правило: **код не менять без явного «да/делай»**.

## 10.08.2026 — прямой DeepSeek рядом с OpenRouter

- Пользователь: ключ DeepSeek API, подключить в бота без обязательного OpenRouter.
- Решение: provider deepseek в llm_gateway (OpenAI-compatible /v1/chat/completions); default остаётся openrouter до явного переключения.
- Секреты только в .env, не в yaml/commit.
- Архив чата строго: .cursor/chats/archive/Chat_10_08_26.md.

## 09.08.2026 — SPIKE P0/P1 и manual hotfix на оба бота

- Пользователь: вчерашний фокус (manual/Companion + SPIKE corridor bypass/pullback) — на **прод и песочницу**.
- Код уже в ветках `09.08.26-*`; на проде включены yaml-ключи, которые 08.08 были OFF «до soak».
- Manual: time-stop/Companion не закрывают `origin=manual`. SPIKE loop на проде не отключать.

## 06.08.2026 — перенос песочницы на прод

- Пользователь: перенести код/настройки песочницы на прод.
- Python почти совпадал; на прод перенесены торговые knobs из sandbox yaml (orderbook_entry и др.).
- Не копировали: .env/Telegram прода, systemd имена, путь SPIKE (прод = signal agent).
- Не брали AW-регресс общего .market_scan.lock — на проде остаётся .spike_scan.lock.

## 06.08.2026 — trailing after BE: tighten −0.5%

- Пользователь: после BE дистанция трейлинга на **0.5 п.п. короче**, оба бота.
- Было: `widen_mult: 1.25` (шире после BE). Стало: `distance_reduce_pct: 0.5`.
- Формула: `max(min_distance_pct, base_distance_pct − 0.5)`.
- Маркер: `Trailing tighten after BE −0.5%`.

## 04.08.2026 — manual daily-loss reset в git

- После Telegram «Сбросить убыток» пишется `data/risk_daily_loss_manual_reset.json` на торговый день (UTC+3 / timezone_offset).
- Пока флаг активен: reconcile с Bybit не перетирает обнулённый PnL и не ставит trade_ok=False.
- На новый день флаг истекает сам. Маркер лога: `MANUAL_DAILY_LOSS_RESET`.
- Order OK: в format-строку добавлен `qty` (был TypeError).

## 02.08.2026 — disk cleanup + keep last bak

- Добавлены server_disk_cleanup.sh / prune_config_backups.sh.
- После каждого нового config.bak install/deploy удаляет все предыдущие bak, кроме последнего.
- Не трогаем .env, live config.yaml, venv, торговые data без явного флага.
- ПК: безопасная чистка __pycache__, старых bak, Temp clones.

# Журнал решений

| Дата | Решение | Почему | Не откатывать |
|------|---------|--------|---------------|
| 19.07 | Hermes OFF | Вирт.TP → советы снять защиту | `hermes.enabled: false` |
| 19.07 | soft ×0.55 AW only | Отрицательный lift soft-правил | weight_overrides sandbox |
| 19.07 | NY skip сб/вс/праздники | Ложный блок сессии акций | skip_weekends/holidays |
| 20.07 | pnl=0 ≠ серия | Безубыток раздувал panic | RiskGuard только pnl < 0 |
| 21.07 | Memory Bank в **git** | Любой ПК после pull | `.cursor/memory-bank/` + alwaysApply |
| 21.07 | Bybit AI ≠ Hermes | Disable Hermes вырезал bybit_monitor | кнопка → `get_bybit_monitor_report()` |
| 21.07 | Trade Companion AW only | Live TP/SL/close; soak до prod | AW `enabled: true`, prod `false` |
| 21.07 | Trade Lifecycle ON | Сбор MFE/MAE/стакан/OB без торговли | `trade_lifecycle.enabled: true` |
| 21.07 | Целостность при disable | Чеклист кнопка↔метод↔config; diff | no-encoding / integrity rules |
| 21.07 | AW notional 30% + own ON | 80% SPIKE съедал депозит; фаза 1 | `max_notional_balance_pct: 30` |
| 22.07 | Дневные ветки 22.07.26-* | Календарь UTC+3 | не продолжать 21.07 |
| 22.07 | GARCH sizing AW only | BeInCrypto/Deutscher: vol→size, не направление | `volatility_regime_sizing` AW true / prod false; оба пути exec |
| 26.07 | SPIKE ≠ opposite own EXIT | DEXE: SPIKE SELL + own Buy → −5.74 | `skip_spike_on_own_signal: true`; маркер skipped SPIKE |
| 01.08 | Размер ×1.5 | Крупнее позиции при том же WR | risk 0.225; notional 120/45; min_risk 0.15 |
| 02.08 | lev min 10 + пол risk | 5× залипало; auto-tune съедал risk до 0.1 | `dynamic_leverage.min: 10`; SelfImprover lo=0.225; verify после install |
| 30.07 | Гайд SSH/FileZilla в git | Доступ восстановлен; не дублировать секреты | `docs/server-access-ssh-filezilla.md`; IP сверять в DO |
| 04.08 | Manual daily-loss reset | reconcile снова блокировал после кнопки | флаг JSON + skip reconcile; `MANUAL_DAILY_LOSS_RESET` |
| 04.08 | Order OK + qty | TypeError в journal при ордере | передать `qty` в logger.info |
| 06.08 | Trailing after BE −0.5% | ужесточить дистанцию после BE, не widen | `distance_reduce_pct: 0.5`; маркер tighten |
| 08.08 | Manual ≠ time-stop/Companion | SNDKUSDT мгновенный close | `manual_auto_close: false`; `auto_close_manual: false` |
| 09.08 | SPIKE P0/P1 оба бота | soak на AW → включить прод | `spike_bypass_no_corridor: true`; `pullback_entry.enabled: true` |

## Сессии

- **30.07 SSH/FileZilla:** IP `207.154.238.178`; при вопросах доступа → гайд в репо; без private key/паролей в git
- **21.07 Memory Bank:** всегда читать под аккаунтом Cursor; авто-UMB; push с дневной веткой
- **21.07 bybit_monitor:** урок — при disable модуля A не удалять B
- **21–22.07 Companion/Lifecycle:** push обе ветки; AW маркеры в journal подтверждены
- **22.07 GARCH:** модуль + wiring orch/SPIKE; тесты 9; ждём push/деплой

## 08.08.2026 — Companion reversal только как flip
- Не отключать Companion. Закрытие по развороту только после prior confirm и ≥5 мин;
  убыток до −3.5% не режем Companion (биржевой SL).
