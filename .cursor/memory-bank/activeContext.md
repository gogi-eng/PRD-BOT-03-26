# Active Context

**Дата фокуса:** 02.08.2026 (UTC+3)  
**Ветки дня:** `02.08.26-PRD-BOT-ALL` @ `62f1946` / `02.08.26-AGENT-WORLD` @ `42c1740`  
**База tip:** `01.08.26-PRD-BOT-ALL` @ `0a1009f` / `01.08.26-AGENT-WORLD` @ `445de52`

## Текущий фокус

1. **Размер / плечо (02.08):** зафиксирован `risk_pct=0.225` (×1.5); `dynamic_leverage.min` **5→10**; пол SelfImprover 0.225 (больше не съедает до 0.1); install проверяет sizing.
2. SPIKE notional: прод **120**, AW **45** (на ветке PRD ошибочно было 120 в sandbox — вернули 45).
3. SSH OK: IP `207.154.238.178`.

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
