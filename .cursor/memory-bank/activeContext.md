# Active Context

**Дата фокуса:** 09.08.2026 (UTC+3)
**Ветки дня:** `09.08.26-AGENT-WORLD` · `09.08.26-PRD-BOT-ALL`

## Текущий фокус

1. **Hotfix 08.08 → оба инстанса (09.08):**
   - Manual/Companion: `manual_auto_close: false`, `auto_close_manual: false` (код уже был в ветках дня)
   - SPIKE P0: `zone_corridor_play.spike_bypass_no_corridor: true` — **прод + песочница**
   - SPIKE P1: `spike_scalp.pullback_entry.enabled: true` — **прод + песочница** (`min_retrace_pct: 0.15`)
   - Прод SPIKE loop: `run_loop_in_signal_agent: true` (не трогать)
2. **SL/TP guard (09.08):** `positions.sl_tp_guard` — восстановление пустых SL/TP на бирже; `include_manual: true`
3. Wallet / SPIKE / polling / фильтры / bybit_monitor — **не отключать**

## Маркеры логов

| Что | Маркер |
|-----|--------|
| Manual time-stop skip | `MANUAL SAFE skip time-stop` |
| Companion skip manual | `Companion skip manage` / `Companion skip close` |
| Zone corridor | `Zone corridor` |
| SPIKE bypass | `SPIKE bypass no_corridor` |
| SPIKE pullback | `SPIKE pullback:` |
| SL/TP guard | `Missing SL/TP on position`, `SL/TP guard` |

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |

## Не смешивать

- /root/PRD-BOT-ALL ← только *-PRD-BOT-ALL
- /root/AGENT-WORLD ← только *-AGENT-WORLD
