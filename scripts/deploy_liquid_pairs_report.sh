#!/usr/bin/env bash
# Deploy hourly liquid pairs reporter into a SEPARATE folder on the server.
# Does NOT touch /root/PRD-BOT-ALL or /root/AGENT-WORLD trading systemd units.
#
# On server:
#   sudo bash scripts/deploy_liquid_pairs_report.sh
#   sudo bash scripts/deploy_liquid_pairs_report.sh /root/LIQUID-PAIRS-REPORT 28.07.26-LIQUID-PAIRS
set -euo pipefail

REPO_DIR="${1:-/root/LIQUID-PAIRS-REPORT}"
BRANCH="${2:-28.07.26-LIQUID-PAIRS}"
REMOTE="${3:-https://github.com/gogi-eng/PRD-BOT-03-26.git}"

echo "=== LIQUID-PAIRS-REPORT deploy ==="
echo "dir=${REPO_DIR} branch=${BRANCH}"
echo "NOTE: trading_bot / telegram_signal_agent will NOT be restarted"

mkdir -p "$(dirname "$REPO_DIR")"

ENV_BACKUP=""
if [[ -f "${REPO_DIR}/.env" ]]; then
  ENV_BACKUP="/tmp/liquid_pairs.env.bak.$(date +%Y%m%d_%H%M%S)"
  cp -a "${REPO_DIR}/.env" "$ENV_BACKUP"
  echo "Backed up .env -> ${ENV_BACKUP}"
fi

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "Shallow clone ${BRANCH} into ${REPO_DIR} ..."
  git clone --depth 1 -b "$BRANCH" "$REMOTE" "$REPO_DIR"
else
  cd "$REPO_DIR"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REMOTE"
  git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" 2>/dev/null || true
  git fetch --depth 1 origin "$BRANCH"
  git reset --hard FETCH_HEAD
  git branch -f "$BRANCH" HEAD 2>/dev/null || true
fi

cd "$REPO_DIR"
SHORT="$(git rev-parse --short HEAD)"
echo "Code: ${SHORT} (branch ${BRANCH})"

# Restore .env if git wipe removed it
if [[ -n "$ENV_BACKUP" && -f "$ENV_BACKUP" ]]; then
  if [[ ! -f "${REPO_DIR}/.env" ]]; then
    cp -a "$ENV_BACKUP" "${REPO_DIR}/.env"
    echo "Restored .env from backup"
  fi
fi

mkdir -p "${REPO_DIR}/data/reports"
chmod +x \
  "${REPO_DIR}/scripts/run_hourly_liquid_pairs.sh" \
  "${REPO_DIR}/scripts/install_liquid_pairs_cron.sh" \
  "${REPO_DIR}/scripts/deploy_liquid_pairs_report.sh" \
  "${REPO_DIR}/scripts/hourly_liquid_pairs_report.py" 2>/dev/null || true

# Cron only ? no systemd restart of trading bots
bash "${REPO_DIR}/scripts/install_liquid_pairs_cron.sh" --dir "$REPO_DIR"

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  cat > "${REPO_DIR}/.env.example" <<'EOF'
# Copy to .env and fill real values (never commit .env):
#   cp .env.example .env && nano .env
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
# Optional: same keys as AGENT-WORLD bot, or a dedicated bot/channel.
EOF
  echo "WARN: no .env yet ? create ${REPO_DIR}/.env with TELEGRAM_TOKEN and TELEGRAM_CHAT_ID"
  echo "      (can copy from AGENT-WORLD: grep TELEGRAM /root/AGENT-WORLD/.env)"
else
  echo ".env present (secrets not printed)"
fi

echo ""
echo "=== Done ==="
echo "Manual test:"
echo "  cd ${REPO_DIR} && bash scripts/run_hourly_liquid_pairs.sh"
echo "Log: ${REPO_DIR}/data/reports/hourly_run.log"
echo "Report: ${REPO_DIR}/data/reports/liquid_pairs_latest.md"
echo "Cron: crontab -l | grep -A2 LIQUID-PAIRS"
