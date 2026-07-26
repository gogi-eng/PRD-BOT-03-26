# Журнал решений

| Дата | Решение | Почему | Не откатывать |
|------|---------|--------|---------------|
| 19.07 | Hermes OFF | Вирт.TP → советы снять защиту | `hermes.enabled: false` |
| 19.07 | soft ×0.55 AW only | Отрицательный lift soft-правил | weight_overrides sandbox |
| 19.07 | NY skip сб/вс/праздники | Ложный блок сессии акций | skip_weekends/holidays |
| 20.07 | pnl=0 ≠ серия | Безубыток раздувал panic | RiskGuard только pnl < 0 |
| 21.07 | Memory Bank в **git** | Любой ПК после pull | `.cursor/memory-bank/` + alwaysApply |
| 21.07 | Bybit AI ≠ Hermes | Disable Hermes вырезал bybit_monitor | кнопка → `get_bybit_monitor_report()` |
| 21.07 | Trade Companion AW only | Live TP/SL/close; soak до prod | AW `enabled: true`, prod `false` |
| 21.07 | Trade Lifecycle ON | Сбор MFE/MAE/стакан/OB без торговли | `trade_lifecycle.enabled: true` |
| 21.07 | Целостность при disable | Чеклист кнопка↔метод↔config; diff | no-encoding / integrity rules |
| 21.07 | AW notional 30% + own ON | 80% SPIKE съедал депозит; фаза 1 | `max_notional_balance_pct: 30` |
| 22.07 | Дневные ветки 22.07.26-* | Календарь UTC+3 | не продолжать 21.07 |
| 22.07 | GARCH sizing AW only | BeInCrypto/Deutscher: vol→size, не направление | `volatility_regime_sizing` AW true / prod false; оба пути exec |
| 26.07 | SPIKE ≠ opposite own EXIT | DEXE: SPIKE SELL + own Buy → −5.74 | `skip_spike_on_own_signal: true`; маркер skipped SPIKE |

## Сессии

- **21.07 Memory Bank:** всегда читать под аккаунтом Cursor; авто-UMB; push с дневной веткой
- **21.07 bybit_monitor:** урок — при disable модуля A не удалять B
- **21–22.07 Companion/Lifecycle:** push обе ветки; AW маркеры в journal подтверждены
- **22.07 GARCH:** модуль + wiring orch/SPIKE; тесты 9; ждём push/деплой
