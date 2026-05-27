# Copy Mirror («попугай») — ветка 27.05.26-Mirror

**Отдельная папка на сервере:** `/root/BOT-Mirror`  
**Не смешивается** с `/root/PRD-BOT-ALL` и `trading_bot`.

## Логика

1. Читает позиции **Copy Trading** (ключ `BYBIT_MIRROR_SOURCE_*`, UID **461368408**).
2. Ждёт **небольшой профит** (по умолчанию **0.12% – 1.5%**).
3. Фильтры **quality_gate** + риск.
4. Зеркало на **субаккаунте 536308614**.
5. Закрытие, когда мастер закрыл позицию на источнике.

---

## Первая установка (новая папка BOT-Mirror)

```bash
# Вариант А: клон вручную
git clone -b 27.05.26-Mirror --single-branch \
  https://github.com/gogi-eng/PRD-BOT-03-26.git /root/BOT-Mirror
cd /root/BOT-Mirror
bash scripts/bootstrap_bot_mirror.sh
```

Скрипт `bootstrap_bot_mirror.sh`:

- создаёт venv;
- копирует `.env` из `/root/PRD-BOT-ALL`, если есть (проверьте ключи MIRROR!);
- ставит `config.copy_mirror.yaml`;
- регистрирует systemd **`copy_mirror`**.

---

## .env в `/root/BOT-Mirror/.env`

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

После правки `.env`:

```bash
sudo systemctl restart copy_mirror
```

---

## Обновление кода (деплой)

```bash
cd /root/BOT-Mirror
bash scripts/server_deploy_mirror.sh
```

---

## Логи

```bash
tail -f /root/BOT-Mirror/copy_mirror.log
sudo journalctl -u copy_mirror -f
```

## Проверка API

```bash
cd /root/BOT-Mirror
./venv/bin/python3 scripts/mirror_copy_probe.py
```

---

## Параметры профита (`config.copy_mirror.yaml`)

```yaml
profit:
  min_pct: 0.12
  max_pct: 1.5
  max_watch_minutes: 45
```

---

## Два бота на сервере

| Папка | Служба | Назначение |
|-------|--------|------------|
| `/root/PRD-BOT-ALL` | `trading_bot` | Основной unified-бот |
| `/root/BOT-Mirror` | `copy_mirror` | Попугай (зеркало копитрейда) |

Они **не мешают** друг другу: разные процессы, конфиги и логи.
