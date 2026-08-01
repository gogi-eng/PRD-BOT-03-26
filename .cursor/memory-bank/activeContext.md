# Active Context

**Дата фокуса:** 01.08.2026 (UTC+3)  
**Ветки дня (для push):** `01.08.26-PRD-BOT-ALL` / `01.08.26-AGENT-WORLD`  
**База tip:** `30.07.26-*` (`65884a0` / `7b4eeca`)

## Текущий фокус

1. **Размер позиций ×1.5** (прод + песочница): risk_pct / пресеты / min_risk_pct; SPIKE notional: прод 80→120, AW 30→45 (на AW было 30% из‑за SPIKE+own). Clamp `signal_notional` до 200%.
2. SSH OK: IP `207.154.238.178`, гайд `docs/server-access-ssh-filezilla.md`.
3. Прод Bybit ключи рабочие (баланс ~$24). AW: дневной лимит $10 уже пробивался 01.08 — после ×1.5 следить за просадкой.

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | `207.154.238.178` |
| User | `root` |
| Прод | `/root/PRD-BOT-ALL` |
| Песочница | `/root/AGENT-WORLD` |

## Не смешивать

- `/root/PRD-BOT-ALL` ← только `*-PRD-BOT-ALL`
- `/root/AGENT-WORLD` ← только `*-AGENT-WORLD`
