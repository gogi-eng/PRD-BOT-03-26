#!/usr/bin/env bash
# Регистрация cron: чекпоинт 30.06.2026 в 10:00 UTC+3 (= 07:00 UTC на сервере).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/venv/bin/python3"
SCRIPT="${ROOT}/scripts/remind_checkpoint_30_06_26.py"
LOG="${ROOT}/data/reminders/cron.log"
# 07:00 UTC = 10:00 Москва/UTC+3
CRON_LINE="0 7 30 6 * cd ${ROOT} && ${PY} ${SCRIPT} >> ${LOG} 2>&1"

mkdir -p "${ROOT}/data/reminders"
(crontab -l 2>/dev/null | grep -v "remind_checkpoint_30_06_26.py"; echo "$CRON_LINE") | crontab -
echo "OK: cron добавлен:"
echo "  $CRON_LINE"
echo "Тест: ${PY} ${SCRIPT} --force"
echo ""
echo "Альтернатива (systemd timer, один раз):"
echo "  sudo cp deploy/checkpoint-30-06-reminder.* /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now checkpoint-30-06-reminder.timer"
