#!/bin/bash
# Сборщик снимков состояния бота (логи + позиции) каждые 15 минут.
# Запускается от имени root.
# Права выполнения: chmod +x /root/AGENT-WORLD/scripts/collect_snapshot.sh
set -u

SNAP_DIR="/root/bot_snapshots"
MARKER="$SNAP_DIR/.last_snapshot"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
SNAP_FILE="$SNAP_DIR/snapshot_${TIMESTAMP}.txt"
PROD_DIR="/root/PRD-BOT-ALL"
SANDBOX_DIR="/root/AGENT-WORLD"

mkdir -p "$SNAP_DIR"

# Пишем всё в файл сразу
exec > "$SNAP_FILE" 2>&1

echo "=========================================="
echo "BOT SNAPSHOT: $TIMESTAMP"
echo "Date: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "Hostname: $(hostname 2>/dev/null || echo unknown)"
echo "=========================================="
echo ""

echo "=== GIT COMMIT HASHES ==="
echo "PROD  ($PROD_DIR): $(git -C "$PROD_DIR" rev-parse --short HEAD 2>/dev/null || echo N/A)"
echo "SANDBOX ($SANDBOX_DIR): $(git -C "$SANDBOX_DIR" rev-parse --short HEAD 2>/dev/null || echo N/A)"
echo ""

SERVICES="trading_bot telegram_signal_agent trading_bot_agent_world telegram_signal_agent_world"

echo "=== SERVICE STATUS ==="
for svc in $SERVICES; do
    active=$(systemctl show -p ActiveState --value "$svc" 2>/dev/null || echo unknown)
    sub=$(systemctl show -p SubState --value "$svc" 2>/dev/null || echo unknown)
    restarts=$(systemctl show -p NRestarts --value "$svc" 2>/dev/null || echo 0)
    echo "$svc: $active/$sub, NRestarts=$restarts"
done
echo ""

log_lines() {
    local svc="$1"
    echo "--- $svc ---"
    if [ -f "$MARKER" ]; then
        local mtime
        mtime=$(date -r "$MARKER" '+%Y-%m-%d %H:%M:%S')
        journalctl -u "$svc" --no-pager --since "$mtime" | tail -n 50
    else
        journalctl -u "$svc" --no-pager --since "15 minutes ago" | tail -n 50
    fi
}

echo "=== JOURNALCTL LAST 50 LINES ==="
for svc in $SERVICES; do
    log_lines "$svc"
    echo ""
done

tail_bot_log() {
    local label="$1"
    local path="$2"
    echo "--- $label: $path ---"
    if [ -f "$path" ]; then
        tail -n 50 "$path"
    else
        echo "File not found: $path"
    fi
}

echo "=== BOT.LOG TAIL (last 50 lines) ==="
tail_bot_log "PROD" "$PROD_DIR/bot.log"
echo ""
tail_bot_log "SANDBOX" "$SANDBOX_DIR/bot.log"
echo ""

grep_summary() {
    local label="$1"
    local path="$2"
    local pattern="$3"
    local n="$4"
    echo "--- $label: pattern='$pattern' ---"
    if [ -f "$path" ]; then
        grep -aE "$pattern" "$path" 2>/dev/null | tail -n "$n" || true
    else
        echo "File not found: $path"
    fi
}

echo "=== OPEN POSITIONS SUMMARY (latest Cycle: ... open=) ==="
grep_summary "PROD" "$PROD_DIR/bot.log" "Cycle:.*open=" 20
echo ""
grep_summary "SANDBOX" "$SANDBOX_DIR/bot.log" "Cycle:.*open=" 20
echo ""

echo "=== API CACHE STATS (latest 'API cycle' lines) ==="
grep_summary "PROD" "$PROD_DIR/bot.log" "API cycle" 20
echo ""
grep_summary "SANDBOX" "$SANDBOX_DIR/bot.log" "API cycle" 20
echo ""

echo "=== DISK SPACE (/root) ==="
df -h /root
echo ""

closed_trades() {
    local label="$1"
    local path="$2"
    echo "--- $label: $path ---"
    if [ -f "$path" ]; then
        grep -ai 'CLOSED' "$path" 2>/dev/null | tail -n 10 || true
    else
        echo "File not found: $path"
    fi
}

echo "=== RECENT CLOSED TRADES (last 10 from trade_history jsonl) ==="
closed_trades "PROD" "$PROD_DIR/data/trade_history.jsonl"
echo ""
closed_trades "SANDBOX" "$SANDBOX_DIR/data/trade_history.jsonl"
echo ""

echo "=== SNAPSHOT COMPLETE: $SNAP_FILE ==="

# Обновляем маркер для следующего запуска
touch "$MARKER"

# Удаляем старые снимки старше 7 дней
find /root/bot_snapshots -maxdepth 1 -type f -name "snapshot_*.txt" -mtime +7 -delete
