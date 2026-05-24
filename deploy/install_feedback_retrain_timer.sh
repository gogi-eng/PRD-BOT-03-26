#!/usr/bin/env bash
# Установка ежедневного переобучения в 00:00 UTC (systemd timer).
set -euo pipefail

REPO_DIR="${1:-/root/PRD-BOT-ALL}"
cd "${REPO_DIR}"

chmod +x "${REPO_DIR}/scripts/daily_feedback_retrain.sh"
chmod +x "${REPO_DIR}/scripts/run_feedback_retrain.sh" 2>/dev/null || true

cp "${REPO_DIR}/deploy/feedback-retrain.service" /etc/systemd/system/feedback-retrain.service
cp "${REPO_DIR}/deploy/feedback-retrain.timer" /etc/systemd/system/feedback-retrain.timer

# Подставить путь репозитория, если не /root/PRD-BOT-ALL
if [[ "${REPO_DIR}" != "/root/PRD-BOT-ALL" ]]; then
  sed -i "s|/root/PRD-BOT-ALL|${REPO_DIR}|g" /etc/systemd/system/feedback-retrain.service
fi

systemctl daemon-reload
systemctl enable --now feedback-retrain.timer
systemctl list-timers feedback-retrain.timer --no-pager

echo ""
echo "OK. Лог переобучения: ${REPO_DIR}/logs/daily_feedback_retrain.log"
echo "Ручной запуск: sudo bash ${REPO_DIR}/scripts/daily_feedback_retrain.sh"
echo "На сервере в config.yaml: feedback_loop.retrain_in_process: false (если используете main.py с transformer)"
