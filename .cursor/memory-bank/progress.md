# Progress

## Сделано (02.08.2026)

### Risk 0.225 + leverage min 10
- [x] `dynamic_leverage.min` 5→10 (prod + AW deploy yaml)
- [x] `risk_pct_per_trade: 0.225` зафиксирован; пресеты/min_risk/notional ×1.5 не откатывали
- [x] AW sandbox notional на ветке PRD: ошибочные 120→**45** (как live AW)
- [x] SelfImprover пол risk 0.1→**0.225** (причина live 0.1 после install)
- [x] `verify_live_sizing_config.py` в install_production / install_agent_world
- [x] Push: `02.08.26-PRD-BOT-ALL` / `02.08.26-AGENT-WORLD` (hash после push)

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
