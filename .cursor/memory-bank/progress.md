# Progress

## Сделано (29.07.2026)

### Разбор LONG EULUSDT (−44 USDT) + ужесточение liquid pairs
- [x] Проверены артефакты: `liquid_pairs_20260729_08/09` → сигнал **SOL SHORT**, EUL отсутствует
- [x] Bybit: утром EUL памп ~06:00 UTC+3 (1.60→1.93) и откаты — волатильный альт
- [x] Ужесточён `decide_liquid_pairs_signal`: BTC/ETH блок LONG альтам, dump-bounce, ALT экстремум 8%, RSI LONG≤70, majors bonus
- [x] Жёстче дисклеймер Telegram/MD («НЕ автоторговля / риск альта»)
- [x] Тесты: `test_hourly_liquid_pairs_signal.py` — **14 passed** (EUL-like кейсы)
- [ ] Commit/push/деплой — по просьбе пользователя

### Trailing after BE (чуть шире после безубытка)
- [x] Модуль `prd_agent/positions/trailing_after_be.py` (`widen_mult`)
- [x] Wiring в `position_steward.py` (единственный путь trailing; SPIKE-exec не ведёт SL)
- [x] AW ON `widen_mult: 1.2` / prod OFF
- [x] Тесты `backend/tests/test_trailing_after_be.py`
- [x] Лог: `Trailing after BE widen`
- [x] Push: AW `af80fa4` · PRD `6977db7`

## Сделано (28.07.2026)

### Hourly liquid pairs → Telegram
- [x] Лайт-сигнал / «почему нет» + Telegram
- [x] Push: AW `485d9a0` · PRD `a7322c5`

### Wallet Tracker v1 (advisory)
- [x] AW ON / prod OFF; telegram_notify AW
- [x] Push watches: AW `f710976` · PRD `b8e7461`
