#!/bin/bash
# Устанавливает cron-задание для сбора снимков каждые 15 минут.
# Запускается от имени root из /root/AGENT-WORLD.
# Права выполнения: chmod +x /root/AGENT-WORLD/scripts/install_snapshot_cron.sh
set -euo pipefail

CRON_JOB='*/15 * * * * /root/AGENT-WORLD/scripts/collect_snapshot.sh >> /root/log_collect_snapshot.log 2>&1'
TMP_CRON=$(mktemp)

# Сохраняем текущий список crontab (может быть пустым)
crontab -l 2>/dev/null > "$TMP_CRON" || true

if grep -F "/root/AGENT-WORLD/scripts/collect_snapshot.sh" "$TMP_CRON" >/dev/null; then
    echo "Cron job already installed. No changes made."
    rm -f "$TMP_CRON"
    exit 0
fi

echo "$CRON_JOB" >> "$TMP_CRON"
crontab "$TMP_CRON"
rm -f "$TMP_CRON"
echo "Cron job installed:"
echo "$CRON_JOB"
echo "Log file: /root/log_collect_snapshot.log"
