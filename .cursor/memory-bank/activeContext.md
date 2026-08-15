# Active Context

**Дата фокуса:** 15.08.2026 (UTC+3)
**Ветки дня:** 15.08.26-PRD-BOT-ALL · 15.08.26-AGENT-WORLD
**Проверка:** песочница active; прод masked/inactive — **не unmask**.

## Важно (правило пользователя)

**Не менять код/config бота без явного «да / делай / одобряю».**
Прод **не** unmask/start без явного «да».

## Текущий фокус (15.08 — AIAI.BY на AW)

1. Провайдер `aiai` в `llm_gateway` (OpenAI-compatible, base `https://api.aiai.by/v1`).
2. Ключ только из `.env`: `AIAI_API_KEY` (или `AIAI_BY_API_KEY`) — **не** в чат/git.
3. AGENT-WORLD: `ai.provider: aiai`, модель по умолчанию `gemini-2.0-flash`.
4. Прод: `ai.provider: openrouter` (masked, не деплоили).
5. После деплоя AW пользователь сам вписывает ключ в `/root/AGENT-WORLD/.env` и рестартит сервисы.

## Маркеры логов

| Что | Маркер |
|-----|--------|
| Manual time-stop skip | MANUAL SAFE skip time-stop |
| Companion skip manual | Companion skip manage / Companion skip close |
| Zone corridor | Zone corridor |
| SPIKE bypass | SPIKE bypass no_corridor |
| SPIKE pullback | SPIKE pullback: |
| SL/TP guard | Missing SL/TP on position, SL/TP guard |
| Manual SL guard | Manual SL missing, Manual SL guard |
| CloseWatchdog | CloseWatchdog / АВАРИЯ ЗАКРЫТИЙ |

**Убрано откатом (не возвращать):** MANUAL SAFE skip SL/TP manage (флаг manage_sl_tp_manual).

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
