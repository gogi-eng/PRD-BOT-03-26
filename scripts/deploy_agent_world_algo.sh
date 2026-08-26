#!/usr/bin/env bash
# Деплой ветки ALGO в /root/AGENT-WORLD для прогона на субаккаунте.
# Использование на сервере:
#   sudo bash scripts/deploy_agent_world_algo.sh
#   sudo bash scripts/deploy_agent_world_algo.sh /root/AGENT-WORLD 12.06.26-ALGO
set -euo pipefail

REPO_DIR="${1:-/root/AGENT-WORLD}"
BRANCH="${2:-21.06.26-AGENT-WORLD}"
REMOTE="${3:-https://github.com/gogi-eng/PRD-BOT-03-26.git}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  echo "Клонируем репозиторий в $REPO_DIR (ветка $BRANCH) ..."
  mkdir -p "$(dirname "$REPO_DIR")"
  git clone -b "$BRANCH" "$REMOTE" "$REPO_DIR"
fi

cd "$REPO_DIR"

if [[ -f config.yaml ]]; then
  cp config.yaml "/root/config.agent_world.bak.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
  # Оставляем только самый новый bak в /root.
  if [[ -f scripts/prune_config_backups.sh ]]; then
    bash scripts/prune_config_backups.sh /root/config.agent_world.bak. || true
  fi
fi

git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REMOTE"
# Старые копии AGENT-WORLD могли иметь fetch только на удалённую ветку 07.05.26_World_Agent
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" 2>/dev/null || true
git fetch origin "$BRANCH"
# FETCH_HEAD надёжнее origin/BRANCH на shallow-клонах без remote-tracking ref
REF="FETCH_HEAD"
git reset --hard "$REF"
# checkout -B иногда падает Bus error на VPS — достаточно указателя ветки на HEAD
git branch -f "$BRANCH" HEAD 2>/dev/null || true
SHORT="$(git rev-parse --short HEAD)"
echo "✓ Код: ${SHORT} (ветка ${BRANCH})"

PYTHON=""
for cand in venv/bin/python3 venv/bin/python .venv/bin/python3; do
  if [[ -x "$REPO_DIR/$cand" ]]; then
    PYTHON="$REPO_DIR/$cand"
    break
  fi
done

# Пустой/битый venv/ (папка есть, python нет) — python3 -m venv падает с File exists
if [[ -z "$PYTHON" && -e "$REPO_DIR/venv" ]]; then
  if [[ -x "$REPO_DIR/.venv/bin/python3" ]]; then
    echo "Битый venv/ — симлинк на рабочий .venv/"
    rm -rf "$REPO_DIR/venv"
    ln -sfn .venv "$REPO_DIR/venv"
    PYTHON="$REPO_DIR/venv/bin/python3"
  else
    echo "Удаляем битый venv/ (нет bin/python3) ..."
    rm -rf "$REPO_DIR/venv"
  fi
fi

if [[ -z "$PYTHON" ]]; then
  echo "Создаём venv ..."
  python3 -m venv venv
  PYTHON="$REPO_DIR/venv/bin/python3"
  "$PYTHON" -m pip install -U pip wheel
  if [[ -f backend/requirements.txt ]]; then
    "$PYTHON" -m pip install -r backend/requirements.txt
  fi
fi

# systemd unit ожидает venv/ — на старых копиях AGENT-WORLD бывает только .venv/
if [[ ! -x "$REPO_DIR/venv/bin/python3" && -x "$REPO_DIR/.venv/bin/python3" ]]; then
  echo "Симлинк venv → .venv (для systemd ExecStart)"
  rm -rf "$REPO_DIR/venv"
  ln -sfn .venv "$REPO_DIR/venv"
  PYTHON="$REPO_DIR/venv/bin/python3"
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "error: не найден интерпретатор: $PYTHON" >&2
  exit 1
fi
echo "✓ Python: $PYTHON"

if [[ -f requirements-unified.txt ]]; then
  "$PYTHON" -m pip install -q -r requirements-unified.txt
fi

bash scripts/install_agent_world_config.sh

mkdir -p reports/world reports/telegram_signals data/ledger data/trades data/kill_switch data

if [[ ! -f .env ]]; then
  echo ""
  echo "⚠️  Создайте $REPO_DIR/.env с ключами СУБАККАУНТА:"
  echo "    BYBIT_API_KEY=..."
  echo "    BYBIT_API_SECRET=..."
  echo "    TELEGRAM_TOKEN=... (уведомления, кнопки отключены в sandbox)"
  echo "    TELEGRAM_CHAT_ID=..."
  echo ""
fi

if [[ -f deploy/trading_bot_agent_world.service ]]; then
  UNIT="/etc/systemd/system/trading_bot_agent_world.service"
  sed -e "s|@REPO_DIR@|$REPO_DIR|g" \
      -e "s|@PYTHON@|$PYTHON|g" \
      deploy/trading_bot_agent_world.service > "$UNIT"
  systemctl daemon-reload
  systemctl enable trading_bot_agent_world
  systemctl restart trading_bot_agent_world
  echo ""
  echo "=== trading_bot_agent_world ==="
  systemctl is-active trading_bot_agent_world || true
  journalctl -u trading_bot_agent_world -n 15 --no-pager || true
else
  echo "Нет deploy/trading_bot_agent_world.service — запуск вручную:"
  echo "  cd $REPO_DIR && ./venv/bin/python3 run_unified.py"
fi

if [[ -f deploy/telegram_signal_agent_world.service ]]; then
  WUNIT="/etc/systemd/system/telegram_signal_agent_world.service"
  sed -e "s|@REPO_DIR@|$REPO_DIR|g" \
      -e "s|@PYTHON@|$PYTHON|g" \
      deploy/telegram_signal_agent_world.service > "$WUNIT"
  systemctl daemon-reload
  # Telethon session AW (отдельный файл; не трогаем session прода).
  if [[ ! -f data/telegram_signal_agent_world.session && -f telegram_user_signal_agent.session ]]; then
    cp -a telegram_user_signal_agent.session data/telegram_signal_agent_world.session
    echo "bootstrap: data/telegram_signal_agent_world.session from local AW session"
  fi
  systemctl enable telegram_signal_agent_world
  systemctl restart telegram_signal_agent_world
  echo ""
  echo "=== telegram_signal_agent_world (channels + RSS) ==="
  systemctl is-active telegram_signal_agent_world || true
  journalctl -u telegram_signal_agent_world -n 10 --no-pager || true
fi

bash scripts/install_agent_world_cron.sh --repo-dir "$REPO_DIR" --every 10

# Cron stop/start ботов по неторговым окнам (UTC сервера).
if [[ -f scripts/install_trading_hours_cron.sh ]]; then
  PROD_ARG=""
  if [[ -d /root/PRD-BOT-ALL ]]; then
    PROD_ARG="--prod-dir /root/PRD-BOT-ALL"
  fi
  bash scripts/install_trading_hours_cron.sh ${PROD_ARG} --world-dir "$REPO_DIR" || {
    echo "warn: install_trading_hours_cron.sh failed — выполните вручную" >&2
  }
fi

echo ""
echo "Готово. Лог: $REPO_DIR/bot.log"
echo "Baseline SKIP: $PYTHON scripts/algo_skip_baseline.py --hours 168"
