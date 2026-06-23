#!/usr/bin/env bash
# Установка Hermes advisor из репозитория в ~/.hermes на VPS
# Запуск: sudo bash deploy/hermes/install-on-server.sh
# Или из корня репо на сервере: bash deploy/hermes/install-on-server.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="${REPO_ROOT}/deploy/hermes"
DEST="${HOME}/.hermes"

mkdir -p "$DEST"/{scripts,skills,prompts,state/history}

for f in config.yaml SOUL.md strategy-goals.yaml; do
  cp -f "$SRC/$f" "$DEST/$f"
done

cp -rf "$SRC/skills/"* "$DEST/skills/"
cp -rf "$SRC/scripts/"* "$DEST/scripts/"
cp -rf "$SRC/prompts/"* "$DEST/prompts/" 2>/dev/null || true
chmod +x "$DEST/scripts/"*.sh 2>/dev/null || true

if [[ ! -f "$DEST/.env" ]]; then
  cp "$SRC/.env.example" "$DEST/.env"
  echo "Создан $DEST/.env — заполните OPENROUTER_API_KEY и TELEGRAM_*"
fi

echo "OK: Hermes advisor → $DEST"
echo "Дальше: bash $DEST/scripts/apply-trading-agent-setup.sh"
