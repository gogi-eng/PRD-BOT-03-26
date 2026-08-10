# Active Context

**Дата фокуса:** 10.08.2026 (UTC+3)
**Ветки дня:** 10.08.26-PRD-BOT-ALL @ 8133bd2 · 10.08.26-AGENT-WORLD @ 336dff4

## Текущий фокус

1. **DeepSeek provider в llm_gateway** (код запушен, default i.provider: openrouter):
   - Включение: .env → DEEPSEEK_API_KEY=... + в config i.provider: deepseek (или PRD_AI_PROVIDER=deepseek)
   - Архив чата: .cursor/chats/archive/Chat_10_08_26.md (id 9cf94508-...)
2. Hotfix 08–09 (manual/Companion, SPIKE P0/P1, SL/TP guard) — без отката
3. Wallet / SPIKE / polling / bybit_monitor — **не отключать**

## Маркеры логов

| Что | Маркер |
|-----|--------|
| Manual time-stop skip | MANUAL SAFE skip time-stop |
| Companion skip manual | Companion skip manage / Companion skip close |
| Zone corridor | Zone corridor |
| SPIKE bypass | SPIKE bypass no_corridor |
| SPIKE pullback | SPIKE pullback: |
| SL/TP guard | Missing SL/TP on position, SL/TP guard |

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
