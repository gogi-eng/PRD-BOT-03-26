# Progress

## Сделано (24.07.2026)

### SPIKE vs 1h тренд (BANKUSDT)
- [x] Разбор: SPIKE = 15m импульс, HTF/1h align в пути входа не было
- [x] Модуль `prd_agent/entry/spike_htf_trend_gate.py` + проводка в `telegram_signal_agent._try_execute_market_setup`
- [x] Config: AW `require_htf_trend_align: true`, prod `false`; intervals `["60"]`
- [x] Тесты `test_spike_htf_trend_gate.py` — green
- [ ] Commit/push/деплой — по просьбе

## Сделано (23.07.2026)

### CBRSUSDT own_multi_agent (volume_guard bypass)
- [x] Анализ: источник OWN, не SPIKE; zone fallback игнорировал volume_guard vol=0
- [x] Блок fallback при volume_guard (`entry_engine_bridge`)
- [x] Soft caution/weak режет size; orch применяет size_mult < 1
- [x] Тесты `test_zone_fallback_volume_guard.py` — 5 passed
- [ ] Commit/push/деплой — по просьбе

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
