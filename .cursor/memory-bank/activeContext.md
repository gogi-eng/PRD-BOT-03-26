# Active Context

**Дата фокуса:** 24.07.2026 (UTC+3)  
**Ветки дня:** веток `24.07.26-*` на remote ещё нет; актуальные tip: `23.07.26-AGENT-WORLD` / `23.07.26-PRD-BOT-ALL`

## Текущий фокус

1. **SPIKE HTF-фильтр (после убытка BANKUSDT)** — код готов, commit/push по просьбе.
   - Причина: SPIKE смотрит только 15m импульс, **не** сверял с 1h трендом.
   - Фикс: `require_htf_trend_align` — блок против HTF (EMA21/55 на 1h).
   - Config: AW `true`, prod `false`.
   - Маркер лога: `SPIKE HTF SYMBOL BUY/SELL: allowed=...` / `skipped spike_htf`.
2. Ранее: reverse/opposite signal EXIT восстановлен (23.07).

## Открытые вопросы / TODO

- [ ] Commit + push в обе дневные ветки (создать `24.07.26-*` при push)
- [ ] Деплой AW: install config + restart → grep `SPIKE HTF` / `spike_htf`
- [ ] После soak на AW — решить, включать ли на проде

## Маркеры логов

- `SPIKE HTF BANKUSDT BUY: allowed=False trend=bearish htf_align: BUY against bearish (60)`
- `Market scanner exec skipped spike_htf: ...`
- `Opposite signal EXIT ...`
- `Volatility regime` / `TRADE COMPANION`
