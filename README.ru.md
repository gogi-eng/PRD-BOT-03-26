# PRD Unified Agent — единый автономный торговый агент

Объединяет лучшие части репозитория [PRD-BOT-03-26](https://github.com/gogi-eng/PRD-BOT-03-26.git) в одну управляемую систему с Telegram-кнопками, риск-менеджментом и отчётами каждые 2 часа.

## Важно честно

**Гарантировать «только прибыль» невозможно.** Бот снижает риск (стоп по дню, серия убытков, размер позиции, кулдауны), но рынок остаётся непредсказуемым. Начинайте с **testnet** или минимального депозита.

## Что внутри

| Модуль | Назначение |
|--------|------------|
| `prd_agent/engine/orchestrator.py` | Главный цикл: сигналы → риск → ордера → отчёт |
| `prd_agent/signals/router.py` | Собственные агенты + приём Telegram-сигналов |
| `prd_agent/risk/guard.py` | Лимиты, кулдауны, размер позиции |
| `prd_agent/reporting/bi_hourly.py` | Отчёт в канал каждые 2 часа |
| `prd_agent/evolution/self_improver.py` | Безопасная подстройка config (критичное — только с одобрением) |
| `prd_agent/telegram/control_bot.py` | Кнопки: старт/стоп/статус/отчёт/emergency |

## Установка (Windows / DigitalOcean)

### 1. Клонировать исходный PRD-репозиторий (обязательно для полной торговли)

```powershell
cd C:\Users\v.dubovik\.cursor\projects\empty-window
git clone https://github.com/gogi-eng/PRD-BOT-03-26.git prd-bot-source
cd prd-bot-source
git fetch --all
git checkout 17.05.26_PRD-TELEGRAM-AGENT
```

Рекомендуемые ветки см. `docs/BRANCH_ANALYSIS.ru.md`.

### 2. Установить unified agent

```powershell
cd C:\Users\Labuh\.vscode\PRD-BOT-ALL
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-unified.txt
copy config.example.yaml config.yaml
```

Заполните в `config.yaml` или `.env`: ключи Bybit (`BYBIT_*`), Telegram (`TELEGRAM_*`), **`TESTNET=true`** для первых запусков.

**Внешний клон `prd-bot-source` больше не нужен** — unified использует код из этой же папки (`agents/`, `exchange/`).

### 3. Проверка testnet (без ордеров)

```powershell
python scripts\verify_testnet.py
```

### 4. Запуск unified

```powershell
python run_unified.py
```

В Telegram отправьте боту `/start` или `/panel` — появятся кнопки. Торговый цикл — кнопка **«Старт торговли»**.

Классический ScalpBot (полный движок): `python main.py`

## Два процесса (как в оригинале)

На сервере часто запускают **два сервиса**:

1. **PRD Telegram Signal Agent** — читает каналы, парсит сигналы (`main.py` без `--trading-bot` в ветке PRD-TELEGRAM-AGENT).
2. **Unified Agent** (этот проект) — торгует, риск, отчёты.

Сигналы из (1) можно передавать в (2) через `SignalRouter.ingest_telegram_signal()` (интеграция через общий файл `data/signals/` или доработка webhook).

## Systemd (пример на Linux)

```ini
[Unit]
Description=PRD Unified Agent
After=network.target

[Service]
WorkingDirectory=/opt/prd-unified-agent
ExecStart=/opt/prd-unified-agent/.venv/bin/python main.py
Restart=always
User=bot

[Install]
WantedBy=multi-user.target
```

## Самоизменение кода — правила безопасности

- **Автоматически**: только числа в `config.yaml` (порог уверенности, кулдаун) в узких пределах + резервная копия в `data/sandbox/`.
- **Вручную**: любые `.py` файлы — копия в sandbox, запись в `data/pending_patches.json`, кнопка одобрения (доработать при необходимости).
- **Откат**: кнопка «Откат config» или восстановление файла из `data/sandbox/config_backup_*.yaml`.

## Тестнет

В `config.yaml`: `bybit.testnet: true` и ключи с testnet.bybit.com.
