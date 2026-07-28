# Active Context

**Дата фокуса:** 28.07.2026 (UTC+3)  
**Ветки дня (для push):** `28.07.26-AGENT-WORLD` · `28.07.26-PRD-BOT-ALL` (общий код wallet_tracker, prod `enabled: false`)  
**База tip:** от `27.07.26-AGENT-WORLD` / лайт SPIKE уже в AW

## Текущий фокус

1. **Wallet Tracker v1 (advisory)** — `prd_agent/analysis/wallet_flow_agent.py`
   - AW: `wallet_tracker.enabled: true`
   - Prod: `enabled: false`
   - Без новых кнопок Telegram; ордера не ставит
   - Docs: `docs/plans/28.07.26-video-onchain-wallet-notes.md`, `docs/wallet_tracker_readme.md`
2. ЛАЙТ SPIKE knobs AW (уже): volume 1.40 / pullback 0.18 / cooldown 2400
3. Opposite hold / HTF / derivatives / SPIKE loops / polling — **не отключать**

## Не смешивать

- `/root/PRD-BOT-ALL` ← только `*-PRD-BOT-ALL`
- `/root/AGENT-WORLD` ← только `*-AGENT-WORLD`
