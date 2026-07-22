# Active Context

**Дата фокуса:** 22.07.2026 (UTC+3)  
**Ветки дня:** `22.07.26-PRD-BOT-ALL` / `22.07.26-AGENT-WORLD`

## Текущий фокус

1. **GARCH volatility regime sizing** — calm/normal/storm → множитель размера позиции.
   - Код: `prd_agent/risk/volatility_regime_sizing.py`
   - Пути: orchestrator `_maybe_execute` **и** `telegram_signal_agent._execute` (SPIKE/scanner)
   - Config: AW `enabled: true`, prod `enabled: false`
   - Маркер лога: `Volatility regime`
2. Memory Bank / чаты — уже в ветках дня (docs push ранее).
3. Trade Companion (AW ON) / Lifecycle / Bybit AI — не трогать.

## Открытые вопросы / TODO

- [ ] **Push + деплой GARCH** (общий код → обе ветки) — ждать явной просьбы пользователя на commit/push
- [ ] После деплоя AW: `grep "Volatility regime"` в journal обоих сервисов + ключ в live `config.yaml`
- [ ] Soak 3–5 дней на AW → решение включить на прод
- [ ] Companion на проде — только после soak

## Недавние решения

| Решение | Где |
|---------|-----|
| GARCH sizing AW ON / prod OFF | `volatility_regime_sizing` |
| Не блокирует вход по умолчанию (`block_on_storm: false`) | только размер |
| SPIKE тоже под множитель (`skip_fast_sources: false`) | оба пути exec |
| Hermes OFF; Bybit AI ≠ Hermes | оба |

## Маркеры логов

- `Volatility regime: GARCH calm/normal/storm включён`
- `Volatility regime BTCUSDT BUY: storm mult=0.50 ...`
- `TRADE COMPANION` / `TRADE LIFECYCLE` (прежние)
