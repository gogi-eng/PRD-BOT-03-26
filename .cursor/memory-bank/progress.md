## 26.08.2026 — AW фильтры чуть уже (меньше сделок)

- Только песочница: `deploy/config.agent_world_sandbox.yaml`
- SPIKE score 72→76, scanner 75→78, TG conf text 85 / AI 60, max_positions 8→6
- Soft overrides 0.55→0.45; A+B каналы / polling / one-way сохранены
- Прод не трогали; откат AW: `02.08.26-AGENT-WORLD`

## 10.08.2026 — DeepSeek + архив Chat_10_08_26

- Прямой DeepSeek в prd_agent/ai/llm_gateway.py (provider deepseek), ключ DEEPSEEK_API_KEY из .env
- Секции deepseek: в deploy yaml + example; default i.provider: openrouter
- Тесты: ackend/tests/test_llm_gateway_deepseek.py (5 passed)
- Чат: .cursor/chats/archive/Chat_10_08_26.md
- Ветки: 10.08.26-AGENT-WORLD @ 336dff4, 10.08.26-PRD-BOT-ALL @ 8133bd2 (cherry-pick)
- Деплой: по запросу; push уже сделан

## 09.08.2026 — hotfix 08.08 на оба инстанса (SPIKE P0/P1 prod ON)

- Код manual/Companion/SPIKE bypass уже был в tip `09.08.26-*` (после 08.08)
- Config: на **проде** включены `spike_bypass_no_corridor: true` и `spike_scalp.pullback_entry.enabled: true` (как на AW)
- Manual/Companion защита уже была в обоих deploy yaml (`manual_auto_close` / `auto_close_manual` = false)
- Тесты: manual + zone_corridor + spike_pullback + pullback_entry + sl_tp_guard — 35 passed
- SPIKE loop прод: `run_loop_in_signal_agent: true` сохранён

## 09.08.2026 — SL/TP guard на открытых позициях

- Модуль `prd_agent/positions/sl_tp_guard.py` + вызов в `PositionSteward.manage()` (до early-return trailing)
- Config `positions.sl_tp_guard` в production и agent_world_sandbox
- При пустых SL/TP на Bybit: WARNING `Missing SL/TP on position` → trading-stop restore → INFO `SL/TP guard`
- Manual тоже (include_manual), без time-stop/Companion close
- Тесты: `backend/tests/test_sl_tp_guard.py`
- Ветки: `09.08.26-PRD-BOT-ALL` + `09.08.26-AGENT-WORLD`

## 08.08.2026 — HOTFIX manual time-stop (SNDKUSDT)

- Причина: adopt manual наследовал opened_at из `_bot_levels` прошлой bot-позиции → мгновенный `close_time_stop`
- Фикс: `positions.manual_auto_close: false`, `trade_companion.auto_close_manual: false`, fresh opened_at for manual, clear `_bot_levels` on drop
- Тесты: `test_manual_position_no_instant_close.py` + companion
- Ветки: `08.08.26-AGENT-WORLD` + `08.08.26-PRD-BOT-ALL`

## 08.08.2026 — P0 SPIKE bypass no_corridor + P1 pullback (AW)

- P0 config: `trading.zone_corridor_play.spike_bypass_no_corridor` (+ min_score/min_move)
- P1: `market_scanner.spike_scalp.pullback_entry` — с 09.08 ON на **обоих** инстансах
- Branches: `08.08.26-AGENT-WORLD`, `08.08.26-PRD-BOT-ALL`

# Progress

## Сделано (06.08.2026)

### Trailing tighten after BE −0.5%
- [x] `trailing_after_be`: вместо widen → `distance_reduce_pct: 0.5` (оба deploy yaml)
- [x] `apply_trailing_after_be_distance`: max(min_floor, base − 0.5)
- [x] Маркер: `Trailing tighten after BE −0.5%`
- [x] Тесты `test_trailing_after_be.py` + py_compile
- [ ] Hash/деплой — см. activeContext после push

## Сделано (04.08.2026)

### Daily-loss reset + Order OK
- [x] `RiskGuard`: флаг ручного сброса до конца торгового дня (`timezone_offset`), skip `reconcile_from_closed_rows`, маркер `MANUAL_DAILY_LOSS_RESET`
- [x] `orchestrator`: Order OK — передан `qty` (нет TypeError в journal)
- [x] Тест `backend/tests/test_reset_daily_loss.py`
- [ ] Hash/деплой — см. activeContext после push

## Сделано (02.08.2026)

### Ежедневный ритуал агента (после 18:00 UTC+3)
- [x] Раздел в плане `dumps/PRD_BOT_stack_program_2026-08-02.txt` (ритуал + изучение логов)
- [x] Rule `daily-stack-program.mdc` (`.vscode` + `PRD-BOT-ALL`) — alwaysApply
- [x] Hook `sessionStart`: `.cursor/hooks.json` + `hooks/daily_stack_ritual.py`
- [x] Маркер `dumps/.daily_ritual_last.txt`; approval gate на код/push/deploy
- [x] В ритуал встроено: SSH journalctl прод+AW → «По логам» + «Предлагаю изменить»
- [ ] Пользователь: каждый вечер смотреть Лабораторию / По дням (фаза 0–1)

### Внедрение оценки (без ослабления фильтров)
- [x] Live config снят с сервера (до): risk 0.225, lev min/max 10–15, Zone ON, GARCH ON, adopt_manual true, trailing_act 2.5, auto_apply true на **обоих**
- [x] Прод: `auto_apply_low_risk: false`; AW auto_apply оставлен true; rate-limit `max_auto_applies_per_hour: 1` в SelfImprover (+ оба deploy yaml)
- [x] «📅 По дням»: раздельно бот / ручные / итог; ключи `analytics.daily_pnl_split_origin`, `analytics.exclude_manual`
- [x] Тесты: `test_daily_pnl_and_lab_reports.py`, `test_self_improver_batch_reload.py`
- [x] Push обе ветки + деплой + grep live `auto_apply_low_risk: false` на проде
  - PRD `02.08.26-PRD-BOT-ALL` @ `2aa04e7` · AW `02.08.26-AGENT-WORLD` @ `f2d6d37`
  - Live: PRD `auto_apply_low_risk: false`, AW `true`; оба `max_auto_applies_per_hour: 1`, `daily_pnl_split_origin: true`
- [ ] Наблюдение 2–3 дня AW (Лаборатория) — см. activeContext

### Disk cleanup + keep last config bak
- [x] scripts/server_disk_cleanup.sh (dry-run / CONFIRM=1)
- [x] scripts/prune_config_backups.sh — keep newest bak
- [x] Встроено в install_production / install_agent_world / deploy_agent_world_algo
- [x] ПК: scripts/pc_bot_folders_cleanup.ps1

### Risk 0.225 + leverage min 10
- [x] `dynamic_leverage.min` 5→10 (prod + AW deploy yaml)
- [x] `risk_pct_per_trade: 0.225` зафиксирован; пресеты/min_risk/notional ×1.5 не откатывали
- [x] AW sandbox notional на ветке PRD: ошибочные 120→**45** (как live AW)
- [x] SelfImprover пол risk 0.1→**0.225** (причина live 0.1 после install)
- [x] `verify_live_sizing_config.py` в install_production / install_agent_world
- [x] Push: `02.08.26-PRD-BOT-ALL` @ `62f1946` · `02.08.26-AGENT-WORLD` @ `42c1740`

## Сделано (01.08.2026)

### Размер позиций ×1.5 (прод + AW)
- [x] `trading.risk_pct_per_trade` 0.15→0.225; пресеты 0.25/0.35/0.45→0.375/0.525/0.675
- [x] `supervisor_v4.min_risk_pct` 0.1→0.15; SPIKE notional: прод 80→120, AW 30→45
- [x] `signal_notional.py`: clamp pct до 200% (раньше 100% резал ×1.5)
- [x] Push: PRD `ba57f07` · AW `4f0cab6` (`01.08.26-*`)

## Сделано (30.07.2026)

### Доступ к DigitalOcean
- [x] Восстановлен SSH/FileZilla (IP `207.154.238.178`, ключ на ПК)
- [x] Гайд: `docs/server-access-ssh-filezilla.md`
- [x] Архив выжимки: `.cursor/chats/archive/30.07.26-ssh-filezilla-access.md`
- [x] Правило: при SSH/FileZilla → сначала гайд в репо
- [x] Push обе ветки `30.07.26-*`: PRD `2983891` · AW `235ac13`

## Сделано (26.07.2026)

### Длинный трейлинг (26.07)
- [x] Config: distance 3.5%, atr 2.2, min 1.8%, adaptive tight 0.85, late_tighten 1.0 / SPIKE 0.90
- [x] deploy prod + AW + live config.yaml (оба workspace)
- [x] Push `26.07.26-*` + деплой (по просьбе)
  - PRD trailing: `919b3a6` · AW: `4dac838`

### Opposite EXIT: не сносить SPIKE own-сигналом
- [x] `opposite_signal_policy.py` + wiring в orchestrator
- [x] Config `skip_spike_on_own_signal: true` (deploy prod + AW + live yaml)
- [x] Тесты 8 passed; маркер `Opposite signal EXIT skipped SPIKE`
- [x] Push обе ветки `26.07.26-*` + деплой (по просьбе)
  - PRD: `8e882ab` · AW: `a6948d3`

## Сделано (21–22.07.2026)

### Memory Bank
- [x] Канон `.cursor/memory-bank/` + `memory-bank.mdc` (alwaysApply)
- [x] Автообновление после значимой работы; UMB; push с дневными ветками
- [x] Сниппет User Rules: `PRD-BOT-ALL/.cursor/USER-RULES-SNIPPET.txt`
- [x] Копии в `.vscode` и `AGENT-WORLD`
- [x] Tip 21.07: PRD `7e5b64d` · AW `49e1473`

### GARCH sizing (22.07, код готов, push по просьбе)
- [x] `prd_agent/risk/volatility_regime_sizing.py` — GARCH(1,1) calm/normal/storm
- [x] Wiring: orchestrator + telegram_signal_agent `_execute`
- [x] Config: AW `enabled: true`, prod `enabled: false`
- [x] Тесты: `backend/tests/test_volatility_regime_sizing.py` (9 passed)
- [ ] Push обе ветки дня + деплой AW (install config + restart)

### Trade / Telegram
- [x] Trade Companion — AW ON, prod OFF (`00bc7ef` / `a17f388`)
- [x] Trade Lifecycle — статистика сделок (`79525be` / `a437238`)
- [x] Restore bybit_monitor после disable Hermes (`fa64398` / `ef37bd0`)
- [x] Правило целостности кода (не вырезать B при disable A)
- [x] AW journal: `TRADE COMPANION` + `TRADE LIFECYCLE` подтверждены
- [x] max_notional_balance_pct 80% (оба); AW own phase: notional 30% + own_agents ON
- [x] Spike на проде: `run_loop_in_signal_agent: true` + отдельный `.spike_scan.lock`
- [x] Trading hours: боты не stop; pre-block закрывает только убыточные; новые входы блоком часов

### Ранее (не откатывать)
- Hermes OFF, soft×0.55 AW, NY weekends, wallet harden, flat-PnL≠consecutive

## Дальше

1. Soak Companion + GARCH на AW 3–5 дней → решение по prod
2. Фаза 2 own+BOS на AW — по PnL own vs SPIKE
3. User Rules сниппет на всех ПК аккаунта (включая пункт про SSH-гайд)

## Не делать без явной просьбы

- Включать Hermes / Companion / GARCH на проде
- Удалять bybit_monitor при правках Hermes
- ESPORTS blacklist без просьбы
- Ослаблять daily loss / max_positions по виртуальным TP Hermes
- Класть в git пароли / private keys / `.env`

## 08.08.2026 — Companion BLESS hotfix
- Причина: trade_companion «разворот» SMA8/21 без flip (не trailing/BE/opposite).
- Правка: prior trend + hold 300s + пороги −3.5%/0.8%; тесты test_trade_companion.py.

