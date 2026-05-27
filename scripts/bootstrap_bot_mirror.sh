#!/usr/bin/env bash
# Первичная установка «попугая» в отдельную папку /root/BOT-Mirror
# Запуск на сервере (один раз):
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/gogi-eng/PRD-BOT-03-26/27.05.26-Mirror/scripts/bootstrap_bot_mirror.sh)" 2>/dev/null || bash scripts/bootstrap_bot_mirror.sh
#
# Или после git clone вручную:
#   cd /root/BOT-Mirror && bash scripts/bootstrap_bot_mirror.sh
set -euo pipefail

INSTALL_DIR="${BOT_MIRROR_DIR:-/root/BOT-Mirror}"
REPO="${BOT_MIRROR_REPO:-https://github.com/gogi-eng/PRD-BOT-03-26.git}"
BRANCH="${BOT_MIRROR_BRANCH:-27.05.26-Mirror}"
MAIN_BOT="${MAIN_BOT_DIR:-/root/PRD-BOT-ALL}"

echo "=== BOT-Mirror (попугай) → $INSTALL_DIR, ветка $BRANCH ==="

if [ ! -d "$INSTALL_DIR/.git" ]; then
  echo "Клонирование репозитория..."
  git clone --branch "$BRANCH" --single-branch "$REPO" "$INSTALL_DIR"
else
  echo "Папка уже есть, обновление git..."
  cd "$INSTALL_DIR"
  git fetch origin
  git checkout -f "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH" "origin/$BRANCH"
  git reset --hard "origin/$BRANCH"
fi

cd "$INSTALL_DIR"
mkdir -p data/copy_mirror

if [ ! -f .env ] && [ -f "$MAIN_BOT/.env" ]; then
  echo "Копирую .env из $MAIN_BOT (проверьте BYBIT_MIRROR_* ключи!)"
  cp -a "$MAIN_BOT/.env" .env
fi

if [ ! -f .env ]; then
  echo ""
  echo "Создайте $INSTALL_DIR/.env с ключами:"
  echo "  BYBIT_MIRROR_SOURCE_KEY / SECRET  (Copy Trading API, UID 461368408)"
  echo "  BYBIT_MIRROR_TARGET_KEY / SECRET  (субаккаунт 536308614)"
  echo "  TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (опционально)"
  echo ""
fi

bash scripts/install_copy_mirror_config.sh
bash scripts/rebuild_venv.sh

echo ""
echo "=== systemd copy_mirror ==="
sudo cp deploy/copy_mirror.service /etc/systemd/system/copy_mirror.service
sudo systemctl daemon-reload
sudo systemctl enable copy_mirror

if [ -f .env ] && grep -q "BYBIT_MIRROR_SOURCE_KEY" .env 2>/dev/null; then
  sudo systemctl restart copy_mirror
  sleep 2
  sudo systemctl status copy_mirror --no-pager | head -12 || true
else
  echo "Служба установлена, но не запущена: допишите .env и выполните:"
  echo "  sudo systemctl start copy_mirror"
fi

echo ""
echo "=== Готово ==="
echo "  Папка:     $INSTALL_DIR"
echo "  Лог:       tail -f $INSTALL_DIR/copy_mirror.log"
echo "  Проверка:  cd $INSTALL_DIR && ./venv/bin/python3 scripts/mirror_copy_probe.py"
echo "  trading_bot в $MAIN_BOT не трогали."
