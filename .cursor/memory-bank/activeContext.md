# Active Context

**Дата фокуса:** 06.08.2026 (UTC+3)  
**Ветки дня:** `06.08.26-AGENT-WORLD` @ `360aebc` · `06.08.26-PRD-BOT-ALL` @ (после push — sync hash)

## Текущий фокус

1. **Сделано 06.08 вечер:** перенос торговых настроек песочницы → прод (`deploy/config.production.yaml`).
   - Добавлено: `orderbook_entry` (ON), `multi_agent_review` (advisory), SPIKE pullback/HTF как на AW
   - Выровнено: opposite exit 20м/68%, `sl_sr_level_index: 1`, `own_agents_enabled: true`, block hours без 4, scan 24, SPIKE vol/cooldown
   - **Оставлено разным:** Telegram/.env прода; SPIKE цикл прод = `run_loop_in_signal_agent: true` (не unified как AW)
   - Python: AW tip не лучше прода (у прода отдельный `.spike_scan.lock`) — код не откатывали
2. Ранее 06.08: trailing after BE −0.5%
3. Wallet / SPIKE / polling / фильтры — **не отключать**

## Сервер

| Параметр | Значение |
|----------|----------|
| IP | 207.154.238.178 |
| User | root |
| Прод | /root/PRD-BOT-ALL |
| Песочница | /root/AGENT-WORLD |

## Не смешивать

- /root/PRD-BOT-ALL ← только *-PRD-BOT-ALL
- /root/AGENT-WORLD ← только *-AGENT-WORLD
