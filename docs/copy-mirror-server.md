# Copy Mirror (pump/dump → signals_inbox)

Служба **не входит** в репозиторий PRD-BOT-ALL. Она живёт на сервере в отдельной папке **Mirror** и пишет сигналы в общий файл inbox.

## Где искать на сервере (DigitalOcean)

```bash
# Папка проекта Mirror (отдельный репозиторий)
ls -la /root/Mirror

# Юнит systemd (имя может отличаться)
systemctl list-units --type=service | grep -i mirror
systemctl status copy_mirror
# или:
systemctl status mirror
```

Типичные пути:

| Что | Путь |
|-----|------|
| Код Mirror | `/root/Mirror` |
| Inbox бота | `/root/PRD-BOT-ALL/reports/telegram_signals/signals_inbox.jsonl` |
| Служба | `copy_mirror.service` |

## Проверка, что pump/dump доходит в inbox

```bash
tail -5 /root/PRD-BOT-ALL/reports/telegram_signals/signals_inbox.jsonl
# Должны быть строки JSON с "source": "mirror_pump_dump_agent"
```

## Если службы нет

1. Уточнить, установлен ли Mirror: `ls /root/Mirror`
2. Если папки нет — клонировать/скопировать проект Mirror с GitHub (ваш репозиторий Mirror).
3. Создать unit по образцу (путь к venv и скрипту смотреть в `/root/Mirror`):

```ini
# /etc/systemd/system/copy_mirror.service
[Unit]
Description=Mirror pump/dump → PRD-BOT inbox
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/Mirror
EnvironmentFile=-/root/Mirror/.env
ExecStart=/root/Mirror/venv/bin/python -m mirror_agent
# или: ExecStart=/root/Mirror/venv/bin/python main.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable copy_mirror
sudo systemctl start copy_mirror
sudo journalctl -u copy_mirror -f
```

## Связь с config.yaml

```yaml
signals:
  telegram_signals_jsonl: reports/telegram_signals/signals_inbox.jsonl
pump_dump_trade:
  enabled: true
  min_confidence: 0.65
```

Бот читает inbox; Mirror только **дописывает** строки в `signals_inbox.jsonl`.
