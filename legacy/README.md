# Legacy (не для прода)

Код в этой папке **не используется** в продакшене.

| Путь | Назначение | Замена |
|------|------------|--------|
| `legacy/bot/` | Старый TradingBot v9 (mixins) | `run_unified.py` → `prd_agent/` |

**Ещё legacy (пока нельзя удалять):** `agents/`, `engine/`, `analysis/` — используются через `prd_agent/` (мост). Перенос — неделя 2.

## Запуск

- **Прод / AGENT-WORLD:** `python run_unified.py` (systemd: `deploy/trading_bot.service`)
- **Только для отладки legacy:** `PRD_LEGACY_BOT=1 python main.py`

Импорт `from bot...` по-прежнему работает через shim в `bot/__init__.py`, но выдаёт `DeprecationWarning`.
