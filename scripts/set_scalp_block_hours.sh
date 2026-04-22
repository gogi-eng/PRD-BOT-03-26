#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-config.yaml}"
PROFILE_HOURS="[5, 6, 8, 12, 13, 21, 23]"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "ERROR: config file not found: $CONFIG_PATH" >&2
  exit 1
fi

BACKUP_PATH="${CONFIG_PATH}.bak.$(date +%Y%m%d_%H%M%S)"
cp "$CONFIG_PATH" "$BACKUP_PATH"

python3 - "$CONFIG_PATH" "$PROFILE_HOURS" <<'PY'
import re
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
hours = sys.argv[2]
text = config_path.read_text(encoding="utf-8")

pattern = r"^(\s*block_entry_utc_hours:\s*)\[[^\]]*\]\s*$"
new_line = r"\g<1>" + hours
updated, count = re.subn(pattern, new_line, text, flags=re.MULTILINE)

if count == 0:
    raise SystemExit("ERROR: key 'block_entry_utc_hours' not found")
if count > 1:
    raise SystemExit("ERROR: multiple 'block_entry_utc_hours' keys found")

config_path.write_text(updated, encoding="utf-8")
PY

echo "OK: applied SCALP profile to $CONFIG_PATH"
echo "Hours: $PROFILE_HOURS"
echo "Backup: $BACKUP_PATH"
