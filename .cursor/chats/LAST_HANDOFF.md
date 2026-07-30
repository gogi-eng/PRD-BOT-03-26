# Последний чат (handoff)

Краткая шпаргалка для продолжения на другом компьютере.

Обновлено: 2026-07-30 (UTC+3)

- **Чат:** `d66d4e5e-8d8d-4f56-97ba-d0f8271e5efc` (выжимка SSH/FileZilla)
- **Заголовок:** Восстановление доступа к DigitalOcean + гайд в репо
- **Файл:** `.cursor/chats/archive/30.07.26-ssh-filezilla-access.md`
- **Гайд:** `docs/server-access-ssh-filezilla.md`

На домашнем/рабочем ПК:

```bash
git pull
# откройте docs/server-access-ssh-filezilla.md или archive/
```

## Фрагмент

Доступ к серверу восстановлен 30.07.2026. IP `207.154.238.178` (сверять в DigitalOcean).
Вход: SFTP/SSH по ключу `id_rsa` с ПК. При потере доступа — Reset Root Password в DO + `authorized_keys`.
Секреты в git не класть.
