# Архив чатов Cursor (PRD-BOT)

Автоматическая копия диалогов Cursor → GitHub, ветка дня `DD.MM.YY-PRD-BOT-ALL`.

## Скрипты

| Файл | Назначение |
|------|------------|
| `scripts/export_cursor_chats.ps1` | Копирует `.jsonl` из Cursor, маскирует токены |
| `scripts/push_chat_archive.ps1` | Экспорт + commit + push только `docs/chat_archive/` |
| `scripts/push_chat_archive_scheduled.ps1` | Для Планировщика: push + сон (standby) |
| `scripts/register-chat-archive-task.ps1` | Планировщик: **будит ПК**, push, **снова сон** (23:55) |

## Ручной запуск

```powershell
cd C:\Users\Labuh\.vscode\PRD-BOT-ALL
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\push_chat_archive.ps1
```

## Регистрация «каждый день» (будит ПК → push → сон)

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register-chat-archive-task.ps1
```

Задача **разбудит** компьютер из сна, отправит чаты в GitHub и **снова уведёт в сон**.

В Windows должно быть включено: **Параметры питания → Сон → Разрешить таймеры пробуждения → Вкл**.

С опцией обновления **каждый час** днём:

```powershell
powershell -File scripts\register-chat-archive-task.ps1 -AlsoHourly
```

## Важно

- Cursor **не сообщает** окончание беседы — поэтому используется расписание (конец дня или каждый час).
- Токены Telegram/API в тексте **заменяются** на `[REDACTED_…]` перед сохранением.
- В git попадает **только** папка `docs/chat_archive/`, не весь проект.

## Папка `qwen/`

Экспорты чатов **Qwen** (план продукта, монетизация). Импорт:

```powershell
scripts\import_qwen_chat.bat
```

См. `docs/chat_archive/qwen/README.ru.md`.
