#!/usr/bin/env bash
# Регистрация cron: напоминание 31.05.2026 в 14:00 (время сервера).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/venv/bin/python3"
SCRIPT="${ROOT}/scripts/remind_medium_priority_work.py"
CRON_LINE="0 14 31 5 * cd ${ROOT} && ${PY} ${SCRIPT} >> ${ROOT}/data/reminders/cron.log 2>&1"

mkdir -p "${ROOT}/data/reminders"
(chrontab -l 2>/dev/null | grep -v "remind_medium_priority_work.py"; echo "$CRON_LINE") | crontab -
echo "OK: cron добавлен:"
echo "  $CRON_LINE"
echo "Проверка вручную: ${PY} ${SCRIPT} --force"
