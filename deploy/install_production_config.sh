#!/usr/bin/env bash
# Обёртка: скрипт живёт в scripts/, не в deploy/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "${ROOT}/scripts/install_production_config.sh" "$@"
