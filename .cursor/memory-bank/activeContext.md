# Active Context

**Дата фокуса:** 22.07.2026 (UTC+3)  
**Ветки дня (создать/использовать):** `22.07.26-PRD-BOT-ALL` / `22.07.26-AGENT-WORLD`  
**Вчера tip (базовый код):** `21.07.26-PRD-BOT-ALL`=`7e5b64d` · `21.07.26-AGENT-WORLD`=`49e1473`

## Текущий фокус

1. **Memory Bank** — читать в начале каждой сессии; автообновлять после значимой работы; UMB = полная синхронизация; push с дневными ветками.
2. **Trade Companion** (AW ON, prod OFF) — сопровождение позиций: TP дальше / закрытие по откату / SL к BE+.
3. **Trade Lifecycle** — статистика сделки (стакан, OB/SMC, MFE/MAE, объём 24h) → `trade_history` + `trade_lifecycle.jsonl`.
4. **Bybit AI** — кнопка `bybit_monitor` восстановлена после случайного удаления при disable Hermes.
5. **Целостность кода** — правило: отключение модуля A не должно вырезать модуль B.

## Открытые вопросы / TODO

- [ ] Подтвердить деплой PROD: Companion/Lifecycle markers + HEAD дня
- [ ] User Rules: вставлен ли сниппет Memory Bank в Cursor Settings
- [ ] ESPORTSUSDT в blacklist AW — только по явной просьбе
- [ ] Companion на проде — только после 3–5 дней soak на AW
- [ ] Новый день → ветки `22.07.26-*` от tip `21.07.26-*`

## Недавние решения (не откатывать)

| Решение | Где |
|---------|-----|
| Hermes OFF (systemd stop/disable) | оба сервера |
| Soft-weights ×0.55 | AW only |
| NY block weekends/holidays | оба |
| Wallet harden: max(balance, wallet) | оба |
| Flat PnL → не consecutive loss | оба |
| Bybit AI ≠ Hermes (не удалять monitor) | оба |
| Trade Companion enabled | AW true / prod false |
| Trade Lifecycle enabled | оба true |
| max_notional_balance_pct: 80 (потом AW own фаза: 30) | смотреть live config |
| Own agents phase-1 на AW | `own_agents_enabled: true` + SPIKE |

## Маркеры логов (проверка после деплоя)

- `TRADE COMPANION: сопровождение открытых сделок включено`
- `TRADE LIFECYCLE: сбор статистики по сделкам включён`
- `Bybit AI` кнопка → отчёт, не «Ошибка кнопки bybit_monitor»

## Ключевые hash (21.07 tip)

| Что | AW | PRD |
|-----|----|-----|
| Memory Bank + rules | `49e1473` | `7e5b64d` |
| Lifecycle (раньше) | `79525be` | `a437238` |
| Companion (раньше) | `00bc7ef` | `a17f388` |
| bybit_monitor restore | `fa64398` | `ef37bd0` |

На AW после restart подтверждено в journal: Companion + Lifecycle включены.
