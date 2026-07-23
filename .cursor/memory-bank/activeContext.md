# Active Context

**Дата фокуса:** 23.07.2026 (UTC+3)  
**Ветки дня:** `23.07.26-PRD-BOT-ALL` / `23.07.26-AGENT-WORLD`

## Текущий фокус

1. **Срочно восстановлено: reverse/opposite signal EXIT** — при обратном сигнале market-close на Bybit.
   - Было в `72f2f4c` (30.06.26), **не попало** в дневные ветки июля → для CBRSUSDT own_multi_agent только skip «позиция открыта», без закрытия.
   - Сейчас: orchestrator + scanner/SPIKE (`close_on_reversal`), config ON прод+AW.
   - Маркер лога: `Opposite signal EXIT SYMBOL ...`
2. Zone fallback volume_guard (уже в tip) — без изменений в этом коммите.

## Открытые вопросы / TODO

- [ ] Деплой обеих веток после push
- [ ] Проверка: `grep "Opposite signal EXIT" bot.log` / journalctl после противоположного сигнала
- [ ] Soak Companion + GARCH на AW

## Маркеры логов

- `Opposite signal EXIT CBRSUSDT open=Buy signal=SELL ...`
- `Zone entry blocked ... volume_guard...`
- `Volatility regime` / `TRADE COMPANION`
