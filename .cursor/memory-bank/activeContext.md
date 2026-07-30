# Active Context

**Дата фокуса:** 30.07.2026 (UTC+3)  
**Ветки дня (для push):** `30.07.26-PRD-BOT-ALL` / `30.07.26-AGENT-WORLD`  
**База tip GitHub:** `29.07.26-*`

## Текущий фокус

1. **Доступ к серверу восстановлен (30.07):** IP `207.154.238.178` (сверять в DO). Гайд: `docs/server-access-ssh-filezilla.md`. Архив: `.cursor/chats/archive/30.07.26-ssh-filezilla-access.md`.
2. При вопросах SSH / FileZilla / пароль сервера — **сначала** указать путь к гайду в репо.
3. Ранее: длинный трейлинг / SPIKE≠opposite EXIT на ветках `26.07+` — не откатывать.

## Сервер (актуально 30.07)

| Параметр | Значение |
|----------|----------|
| IP | `207.154.238.178` (проверять в DigitalOcean) |
| User | `root` |
| Прод | `/root/PRD-BOT-ALL` |
| Песочница | `/root/AGENT-WORLD` |

Вход предпочтительно по SSH-ключу с ПК (`id_rsa`), не по паролю в чат.

## Не смешивать

- `/root/PRD-BOT-ALL` ← только `*-PRD-BOT-ALL`
- `/root/AGENT-WORLD` ← только `*-AGENT-WORLD`
