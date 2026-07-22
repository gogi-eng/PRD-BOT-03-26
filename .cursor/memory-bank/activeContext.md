# Active Context

**Дата фокуса:** 22.07.2026 (UTC+3)  
**Ветки дня:** .07.26-PRD-BOT-ALL\ / .07.26-AGENT-WORLD
## Текущий фокус

1. **Прод = алгоритмы песочницы** (config.production.yaml):
   - Companion ON, GARCH ON, Zone corridor ON (+SPIKE), derivatives advisory ON
   - risk_pct 0.15, leverage 10, dynamic 5–15
   - entry_pipeline regime_thresholds ON; own_agents OFF (как AW spike-focus)
   - SPIKE цикл прода: un_loop_in_signal_agent: true\ (не менять)
2. Песочница: \ca913fc\ — уже OK
3. Прод код tip до этого пуша: dd9ffe\; после пуша algos — новый hash

## Не смешивать папки/ветки

- \/root/PRD-BOT-ALL\ ← только \*-PRD-BOT-ALL- \/root/AGENT-WORLD\ ← только \*-AGENT-WORLD