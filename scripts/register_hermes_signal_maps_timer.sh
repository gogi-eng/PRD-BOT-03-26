#!/usr/bin/env bash
# Таймер: карты сигналов → Analise_Hermes каждые 3 часа.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/venv/bin/python3"
SCRIPT="${ROOT}/scripts/hermes_signal_maps_sync.py"
LOG="${ROOT}/data/reminders/hermes_signal_maps.log"

mkdir -p "${ROOT}/data/reminders"
echo "Установка systemd timer (каждые 3 ч)..."
sudo cp "${ROOT}/deploy/hermes/hermes-signal-maps.service" /etc/systemd/system/
sudo cp "${ROOT}/deploy/hermes/hermes-signal-maps.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-signal-maps.timer
echo "OK. Проверка:"
sudo systemctl list-timers | grep hermes-signal-maps || true
echo ""
echo "Ручной тест:"
echo "  cd ${ROOT} && HERMES_GITHUB_DIR=/root/Analise_Hermes ${PY} ${SCRIPT} --git-clone --git-push >> ${LOG} 2>&1"
