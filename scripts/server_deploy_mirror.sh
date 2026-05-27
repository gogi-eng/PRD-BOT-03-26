#!/usr/bin/env bash
# Обновление попугая в /root/BOT-Mirror (сохраняет .env и config.copy_mirror.yaml)
#   cd /root/BOT-Mirror && bash scripts/server_deploy_mirror.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BRANCH="${1:-27.05.26-Mirror}"
BACKUP="$HOME/BOT-Mirror-backup-$(date +%Y%m%d_%H%M%S)"

echo "=== Резервная копия ==="
mkdir -p "$BACKUP"
for f in .env config.copy_mirror.yaml; do
  if [ -e "$f" ]; then
    cp -a "$f" "$BACKUP/"
    echo "  $f"
  fi
done

echo ""
echo "=== Git → origin/$BRANCH ==="
git fetch origin
git checkout -f "$BRANCH" 2>/dev/null || git checkout -B "$BRANCH" "origin/$BRANCH"
git reset --hard "origin/$BRANCH"
git clean -fd \
  -e .env -e config.copy_mirror.yaml \
  -e venv -e .venv -e 'venv.bak.*' \
  -e data -e copy_mirror.log

echo ""
echo "=== Восстановление настроек ==="
[ -f "$BACKUP/.env" ] && cp -a "$BACKUP/.env" .env && echo "  .env"
[ -f "$BACKUP/config.copy_mirror.yaml" ] && cp -a "$BACKUP/config.copy_mirror.yaml" config.copy_mirror.yaml && echo "  config.copy_mirror.yaml"

bash scripts/install_copy_mirror_config.sh
bash scripts/rebuild_venv.sh

sudo cp deploy/copy_mirror.service /etc/systemd/system/copy_mirror.service
sudo systemctl daemon-reload
sudo systemctl restart copy_mirror
sleep 2
sudo systemctl status copy_mirror --no-pager | head -12 || true

echo ""
echo "=== Готово. Резервная копия: $BACKUP ==="
echo "Лог: tail -f $ROOT/copy_mirror.log"
