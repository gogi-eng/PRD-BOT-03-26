# Copy Mirror — отдельный сервис (ветка 27.05.26-Mirror)

**Не смешивается** с `trading_bot` / `run_unified.py`. Свой процесс: `run_copy_mirror.py`.

## Логика

1. Читает позиции **Copy Trading** (ключ `BYBIT_MIRROR_SOURCE_*`, UID **461368408**).
2. Ждёт **небольшой профит** (по умолчанию **0.12% – 1.5%** от входа).
3. Пропускает через **quality_gate** + лимиты риска.
4. Открывает зеркало на **субаккаунте** **536308614** (`BYBIT_MIRROR_TARGET_*`).
5. Закрывает зеркало, когда позиция на источнике исчезла.

## .env на сервере

```env
BYBIT_MAIN_UID=461368408
BYBIT_SUB_UID=536308614
BYBIT_MIRROR_SOURCE_KEY=ключ_Copy_Trading_API
BYBIT_MIRROR_SOURCE_SECRET=...
BYBIT_MIRROR_TARGET_KEY=ключ_субаккаунта
BYBIT_MIRROR_TARGET_SECRET=...
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Установка

```bash
cd /root/PRD-BOT-ALL
git fetch origin
git checkout 27.05.26-Mirror
git pull origin 27.05.26-Mirror
bash scripts/install_copy_mirror_config.sh
sudo cp deploy/copy_mirror.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now copy_mirror
```

## Логи

```bash
tail -f /root/PRD-BOT-ALL/copy_mirror.log
sudo journalctl -u copy_mirror -f
```

## Проверка API

```bash
./venv/bin/python3 scripts/mirror_copy_probe.py
```

В блоке **Copy Trading API** должны быть позиции, если мастера открыли сделки.

## Параметры профита (`config.copy_mirror.yaml`)

```yaml
profit:
  min_pct: 0.12   # минимум плюса, чтобы зеркалить
  max_pct: 1.5    # если больше — «опоздали», не гонимся
  max_watch_minutes: 45
```
