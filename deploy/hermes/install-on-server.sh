#!/usr/bin/env bash
# УСТАРЕЛО (19.07.26): Hermes advisor ОТКЛЮЧЁН в боте (hermes.enabled: false).
# Не устанавливайте gateway на новые серверы.
#
# Остановить старый Hermes на сервере:
#   systemctl --user stop hermes-gateway 2>/dev/null || true
#   systemctl --user disable hermes-gateway 2>/dev/null || true
#   sudo systemctl stop hermes-cursor-feed hermes-signal-maps 2>/dev/null || true
#   sudo systemctl disable hermes-cursor-feed hermes-signal-maps 2>/dev/null || true
set -euo pipefail
echo "ОТКАЗ: Hermes отключён (19.07.26). Установка отменена."
echo "Команды остановки сервисов — в шапке этого скрипта."
exit 1
