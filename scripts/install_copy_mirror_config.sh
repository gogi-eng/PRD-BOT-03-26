#!/usr/bin/env bash
# Установка config.copy_mirror.yaml на сервере (не трогает config.yaml бота)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -f config.copy_mirror.yaml ]]; then
  cp deploy/config.copy_mirror.yaml config.copy_mirror.yaml
  echo "Создан config.copy_mirror.yaml — проверьте .env (MIRROR SOURCE/TARGET keys)"
else
  echo "config.copy_mirror.yaml уже есть"
fi
mkdir -p data/copy_mirror
