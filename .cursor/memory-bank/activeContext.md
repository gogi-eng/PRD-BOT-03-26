# Active Context

**Дата фокуса:** 11.08.2026 (UTC+3)
**Ветки дня:** 11.08.26-PRD-BOT-ALL @ e2599fd · 11.08.26-AGENT-WORLD @ 6e99c9f

## Важно (правило пользователя)

**Не менять код/config бота без явного «да / делай / одобряю».**
Исключение 11.08 «СРОЧНО ИСПРАВЛЯЙ» отменено откатом по просьбе пользователя («верни как было»).

## Текущий фокус

1. **Откат SL/TP manual-safe (11.08):** revert 8f940c / e283f9b — снова управляем SL/TP trailing/BE+ для всех позиций (в т.ч. подхваченных), как ~09–10.08.
2. DeepSeek provider: код есть, default i.provider: openrouter — включать только по явному «да».
3. Manual: time-stop/Companion по-прежнему НЕ закрывают origin=manual (manual_auto_close: false).
4. Wallet / SPIKE / polling / bybit_monitor — **не отключать**.

## Маркеры логов

| Что | Маркер |
|-----|--------|
| Manual time-stop skip | MANUAL SAFE skip time-stop |
| Companion skip manual | Companion skip manage / Companion skip close |
| Zone corridor | Zone corridor |
| SPIKE bypass | SPIKE bypass no_corridor |
| SPIKE pullback | SPIKE pullback: |
| SL/TP guard | Missing SL/TP on position, SL/TP guard |

**Убрано откатом:** MANUAL SAFE skip SL/TP manage (флаг manage_sl_tp_manual).

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
