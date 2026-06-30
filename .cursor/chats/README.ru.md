# Чаты Cursor в репозитории

Cursor **не синхронизирует** чаты между компьютерами. Эта папка — обходной путь.

## Как работает

Скрипт `scripts/sync_cursor_chats.py` копирует локальные транскрипты Cursor в:

- `archive/*.md` — полные чаты
- `INDEX.md` — список всех чатов
- `LAST_HANDOFF.md` — последний чат (для быстрого старта)

## Запуск вручную

```powershell
cd C:\Users\ВАШ_НИК\.vscode\PRD-BOT-26.05.26-ALL
powershell -ExecutionPolicy Bypass -File scripts\sync_cursor_chats.ps1 -Push
```

На домашнем ПК после работы:

```powershell
git pull
```

Откройте `INDEX.md` или нужный файл в `archive/`.

## Автоматически (Windows)

Один раз:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_cursor_chats_sync_task.ps1
```

Каждые 30 минут чаты сохраняются и отправляются на GitHub (если есть изменения).

## Важно

- Закрывать Cursor перед копированием **не обязательно** (читаем только `.jsonl`).
- Секреты из чатов в git не попадают специально — но проверяйте, что не пишете ключи в чат.
- На **двух ПК** установите задачу на обоих — тогда история собирается с обеих машин.
