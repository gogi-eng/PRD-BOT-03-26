# Progress

## Сделано (28.07.2026)

### Hourly liquid pairs → Telegram
- [x] Лайт-сигнал / «почему нет» в конце `liquid_pairs_latest.md` + JSON
- [x] Отправка Bot API (`--telegram` / `LIQUID_PAIRS_TELEGRAM=1`); PS1 hourly включает `--telegram`
- [x] Тесты: `backend/tests/test_hourly_liquid_pairs_signal.py` (10 passed)
- [ ] Push обе ветки `28.07.26-*` (по просьбе)

### Wallet Tracker v1 (advisory on-chain watches)
- [x] Код: `prd_agent/analysis/wallet_flow_agent.py` (Debank/Etherscan/Stub)
- [x] Orchestrator: init + loop + `get_wallet_tracker_report` + soft match log
- [x] Config AW ON / prod OFF
- [x] Telegram советы: `telegram_notify` + `notifier.wallet_flow_advice` (дедуп symbol+side)
- [x] Docs: video notes + wallet_tracker_readme + план лайта фаза 1.5
- [x] Тесты: `backend/tests/test_wallet_tracker_agent.py`
- [x] Push AW `bcf5dba` + PRD `5780be2` (telegram_notify; prod enabled:false)
- [x] watches: 9 публичных адресов в AW + prod yaml (prod цикл выкл.); readme про watches
- [x] Push watches: AW `f710976` · PRD `b8e7461`

### ЛАЙТ песочница (только AGENT-WORLD)
- [x] План: `docs/plans/28.07.26-agent-world-lite-sandbox.md`
- [x] Config AW: `min_volume_ratio` 1.40, `min_retrace_pct` 0.18, `symbol_cooldown_sec` 2400
- [x] Прод yaml **не** трогали (лайт knobs)
- [ ] Push / деплой по просьбе
## Сделано (26.07.2026)

### Длинный трейлинг (26.07)
- [x] Config: distance 3.5%, atr 2.2, min 1.8%, adaptive tight 0.85, late_tighten 1.0 / SPIKE 0.90
- [x] deploy prod + AW + live config.yaml (оба workspace)
- [ ] Push `26.07.26-*` + деплой (по просьбе)

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

1. Push/деплой GARCH → проверка маркера `Volatility regime` на AW
2. Soak Companion + GARCH на AW 3–5 дней → решение по prod
3. Фаза 2 own+BOS на AW — по PnL own vs SPIKE
4. User Rules сниппет на всех ПК аккаунта

## Не делать без явной просьбы

- Включать Hermes / Companion / GARCH на проде
- Удалять bybit_monitor при правках Hermes
- ESPORTS blacklist без просьбы
- Ослаблять daily loss / max_positions по виртуальным TP Hermes
