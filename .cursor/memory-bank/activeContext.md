# Active Context

**Дата фокуса:** 23.07.2026 (UTC+3)  
**Ветки дня:** 23.07.26-PRD-BOT-ALL / 23.07.26-AGENT-WORLD

## Текущий фокус

1. **Hotfix CBRSUSDT (own_multi_agent):** zone fallback обходил olume_guard (vol=0) → ENTERED full size при soft caution 47.
   - Фикс: should_block_zone_entry_fallback в entry_engine_bridge.py
   - Soft caution/weak режет size_mult; orchestrator применяет cut <1
   - Тест: ackend/tests/test_zone_fallback_volume_guard.py (5 passed)
   - Commit/push в обе ветки дня выполнен
2. SPIKE pullback / Companion / Bybit AI — не трогать (целостность OK).

## Не смешивать инстанс/ветки

- /root/PRD-BOT-ALL → только *-PRD-BOT-ALL
- /root/AGENT-WORLD → только *-AGENT-WORLD
- [ ] Деплой AW: маркер Zone entry blocked / отсутствие ENTERED при volume_guard
- [ ] Soak 3–5 дней Companion + GARCH на AW

## Связанные решения

| Решение | Где |
|---------|-----|
| volume_guard → блок zone fallback (прод+AW) | entry_engine_bridge |
| soft caution+spread_wide → size_mult ≤0.35 | entry_soft_rules + orch |
| GARCH AW ON / prod OFF | olatility_regime_sizing |

## Маркеры логов

- Zone entry blocked ... hard guard fallback denied (volume_guard...)
- Soft score ... (caution) ... size_mult=0.350
- Volatility regime / TRADE COMPANION (прежние)
