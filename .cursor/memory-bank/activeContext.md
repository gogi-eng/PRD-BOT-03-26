# Active Context

**Дата фокуса:** 08.08.2026 (UTC+3)
**Ветки дня:** `08.08.26-AGENT-WORLD` · `08.08.26-PRD-BOT-ALL`

## Текущий фокус

1. **HOTFIX BLESS:** Companion ложный «разворот» SMA закрыл Long на откате (прод −$0.43).
   - Фикс: `require_prior_trend` (flip), `min_hold_sec=300`, max_loss −1.5→−3.5, min_profit 0.3→0.8
2. Ранее: P0 spike_bypass_no_corridor (AW); P1 pullback_entry; trailing after BE −0.5%
3. Wallet / SPIKE / polling — **не отключать**

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |
