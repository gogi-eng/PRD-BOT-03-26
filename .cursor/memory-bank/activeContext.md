# Active Context

**Дата фокуса:** 26.07.2026 (UTC+3)  
**Ветки дня (для push):** `26.07.26-PRD-BOT-ALL` / `26.07.26-AGENT-WORLD`  
**База tip GitHub:** `24.07.26-*` (на GitHub ещё нет 25/26)

## Текущий фокус

1. **SPIKE ≠ opposite own EXIT** (готово локально, push по просьбе):
   - `opposite_signal_exit.skip_spike_on_own_signal: true` (default ON)
   - Модуль `prd_agent/positions/opposite_signal_policy.py`
   - Маркер лога: `Opposite signal EXIT skipped SPIKE`
   - Тесты: `test_opposite_signal_spike_skip.py` (8 passed)
   - Причина: DEXE 24.07 — SPIKE SELL закрыт own Buy (−5.74 USDT)

2. OmniRoute на ПК — только Chat Cursor, не Agent; боты не трогаем.

3. Прод/песочница: алгоритмы выровнены ранее (Companion, GARCH, Zone, SPIKE loops).

## Не смешивать

- `/root/PRD-BOT-ALL` ← только `*-PRD-BOT-ALL`
- `/root/AGENT-WORLD` ← только `*-AGENT-WORLD`
