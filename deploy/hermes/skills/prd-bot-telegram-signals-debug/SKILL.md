---
name: prd-bot-telegram-signals-debug
description: Workflow for debugging issues with Telegram signals in PRD-BOT-ALL.
triggers:
  - user asks to troubleshoot, debug, or evaluate Telegram signals
  - user reports missing or unexecuted Telegram signals
---

## Отладка Telegram-сигналов и работы агента PRD-BOT-ALL

### Проблема: Ошибки подключения/аутентификации Telethon

**Симптомы:**
- `sqlite3.OperationalError: database is locked`: Вызывается множественными экземплярами `telegram_signal_agent.py`, конкурирующими за один и тот же файл сессии. Это может произойти, если ручные перезапуски накладываются на автоматические перезапуски `systemd`.
- `telethon.errors.common.TypeNotFoundError`: Указывает на устаревшую библиотеку `telethon` или поврежденный/несовместимый файл сессии.
- `EOFError: EOF when reading a line`: Агент, запущенный в фоновом режиме, пытается выполнить интерактивный вход (например, запросить номер телефона или код 2FA для пользовательской учетной записи), но не может получить ввод).

### Шаги решения:

1.  **Убедитесь, что `telethon` обновлен:**
    ```bash
    pip install --upgrade telethon
    ```

2.  **Остановите ВСЕ конфликтующие процессы Telegram-агента:**
    Используйте `pkill -f telegram_signal_agent.py` для завершения всех экземпляров. Это критически важно перед очисткой или попытками интерактивного входа.

3.  **Очистите файлы сессий Telethon и состояние агента:**
    -   **Определите файлы сессий Telethon**: Эти файлы обычно называются `anon.session`, `<session_name>.session` или `telegram_user_signal_agent.session` (и связанные с ними файлы `-journal`). Они обычно находятся в `/root/PRD-BOT-ALL/` или `/root/PRD-BOT-ALL/data/`.
    -   **Удалите их**: `rm -f /root/PRD-BOT-ALL/*.session* /root/PRD-BOT-ALL/data/*.session*`
    -   **Удалите файл состояния агента**: `rm -f /root/PRD-BOT-ALL/telegram_signal_agent_state.json`. Это очищает все постоянные внутренние состояния, включая потенциальные блокировки, связанные с убытками, или другие застрявшие флаги.

4.  **Подтвердите метод аутентификации Telethon (критично для `EOFError`):
    -   Реализация `telegram_signal_agent.py` использует `os.getenv("TELEGRAM_API_ID")` и `os.getenv("TELEGRAM_API_HASH")`.
    -   Это подтверждает, что агент настроен на **вход в учетную запись пользователя**, а не на вход по токену бота.Настройка `bot_token: ${TELEGRAM_BOT_TOKEN}` в `config.yaml` относится к другим частям бота (например, к unified-боту для отправки уведомлений) и не имеет отношения к аутентификации этого агента для получения сообщений из каналов).

5.  **Настройте `.env` с учетными данными API:**
    -   Убедитесь, что ваш файл `/root/PRD-BOT-ALL/.env` содержит действительные `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`. Их можно получить, зарегистрировав свое приложение на [my.telegram.org](https://my.telegram.org/).

6.  **Выполните одноразовый интерактивный вход (только для учетных записей пользователей):**
    -   После очистки файлов сессий и настройки `.env` вы **должны** выполнить интерактивный вход один раз, чтобы сгенерировать файл сессии (`telegram_user_signal_agent.session`).
    -   **Шаги для интерактивного входа:**
        a.  Убедитесь, что никакие процессы `telegram_signal_agent.py` не запущены.
        b.  Перейдите в каталог `/root/PRD-BOT-ALL/` (или туда, где активен ваш `venv`).
        c.  Активируйте ваше виртуальное окружение (например, `source venv/bin/activate`).
        d.  Запустите агент непосредственно в интерактивном режиме в терминале, возможно с аргументом для однократного запуска и выхода:
            ```bash
            python3 scripts/telegram_signal_agent.py --once
            ```
            *(Примечание: Возможно, вам потребуется явно экспортировать переменные окружения, если скрипт не подхватывает их: `TELEGRAM_API_ID=XXXX TELEGRAM_API_HASH=YYYY python3 ...`)*
        e.  При появлении запроса введите свой номер телефона Telegram и код подтверждения. После успешного входа файл сессии (`telegram_user_signal_agent.session`) будет создан в каталоге скрипта (или как настроено).

7.  **Проверьте автоматический перезапуск:**
    -   После того, как интерактивный вход успешно создаст файл сессии, диспетчер процессов вашей системы (например, `systemd`) должен иметь возможность автоматически перезапускать `telegram_signal_agent.py` в фоновом режиме без `EOFError`.

### Проверка:

-   Проверьте `telegram_signal_agent.log` на наличие свежих записей, новых сообщений `started:`, а также на **отсутствие** `database is locked`, `TypeNotFoundError` и `EOFError`.
-   Убедитесь, что `signals_inbox.jsonl` (находящийся в `reports/telegram_signals/` в `PRD-BOT-ALL`) начинает получать обработанные сигналы.

### Проблема: Бот не получает данные о сделках с Bybit (ошибки get_recent_trades)

**Симптомы:**
- `WARNING prd_agent.signals: collect_all own failed: 'BybitAdapter' object has no attribute 'get_recent_trades'` в `bot.log`.
- Бот не открывает новые позиции или торгует менее эффективно.

**Причина:**
- `BybitAdapter` (файл `/root/PRD-BOT-ALL/prd_agent/exchange/bybit_adapter.py`) не может найти функцию `get_recent_trades` внутри используемого `_client`. Это происходит, когда из-за некорректного импорта используется упрощенный клиент `SimpleBybitClient` вместо полноценного `BybitClient`.

**Решение:**
1.  **Исправьте импорт `BybitClient`:** Откройте файл `/root/PRD-BOT-ALL/prd_agent/exchange/bybit_adapter.py`.
2.  Найдите функцию `_import_local_client` (примерно строка 14-23).
3.  Измените строку импорта внутри `try` блока:
    -   Было: `from exchange.bybit_client import BybitClient`
    -   Станет: `from prd_agent.exchange.bybit_client import BybitClient`
4.  **Перезапустите бота PRD-BOT-ALL** для применения изменений.