# Active Context

**Дата фокуса:** 28.07.2026 (UTC+3)  
**Ветки дня (для push):** `28.07.26-AGENT-WORLD` · `28.07.26-PRD-BOT-ALL` (общий код wallet_tracker, prod `enabled: false`)  
**База tip:** AW от лайта 28.07; PRD от `27.07.26-PRD-BOT-ALL` + cherry-pick wallet_tracker

## Текущий фокус

1. **Wallet Tracker v1 (advisory)** — `prd_agent/analysis/wallet_flow_agent.py`
   - AW: `enabled: true`, `telegram_notify: true` — советы LONG/SHORT в Telegram через notifier
   - Prod: `enabled: false`, `telegram_notify: false`
   - Без новых кнопок Telegram; ордера не ставит; дедуп symbol+side + cooldown
   - Docs: `docs/plans/28.07.26-video-onchain-wallet-notes.md`, `docs/wallet_tracker_readme.md`
2. ЛАЙТ SPIKE knobs AW: volume 1.40 / pullback 0.18 / cooldown 2400
3. Opposite hold / HTF / derivatives / SPIKE loops / polling — **не отключать**

## Не смешивать

- `/root/PRD-BOT-ALL` ← только `*-PRD-BOT-ALL`
- `/root/AGENT-WORLD` ← только `*-AGENT-WORLD`
