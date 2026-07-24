# Active Context

**Дата фокуса:** 24.07.2026 (UTC+3)  
**Ветки дня:** `24.07.26-AGENT-WORLD` (локально); tip HTF+SR — не закоммичен до просьбы

## Текущий фокус

1. **SPIKE HTF + S/R контекст** — код готов, commit/push по просьбе.
   - База: `require_htf_trend_align` — блок против 1h EMA21/55.
   - Новое: при `htf_sr_context_enabled` против тренда **разрешить**, если:
     - разворот у S/R (BUY у support / SELL у resistance), или
     - пробой уровня в сторону сигнала (продолжение).
   - Без S/R-контекста против тренда — **блок** (как BANKUSDT).
   - Config: AW `require_htf_trend_align: true` + `htf_sr_context_enabled: true`; prod оба/align `false`, sr `false`.
2. Ранее: reverse/opposite signal EXIT восстановлен (23.07).

## Открытые вопросы / TODO

- [ ] Commit + push в обе дневные ветки (`24.07.26-*`)
- [ ] Деплой AW: install config + restart → grep `SPIKE HTF` / `near support` / `broke` / `no SR context`
- [ ] После soak на AW — решить, включать ли на проде

## Маркеры логов

- `SPIKE HTF SYMBOL BUY: allowed=True ... against bearish but near support ... → allow`
- `SPIKE HTF SYMBOL BUY: allowed=True ... against bearish but broke resistance ... → allow`
- `SPIKE HTF SYMBOL BUY: allowed=False ... against bearish + no SR context → block`
- `Market scanner exec skipped spike_htf: ...`
- `Opposite signal EXIT ...`
- `Volatility regime` / `TRADE COMPANION`
