#!/usr/bin/env bash
# Добавляет exit_management и feedback_loop train keys в config.yaml (без полной замены файла).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/venv/bin/python3"

"${PY}" - <<'PY'
from pathlib import Path
import yaml

p = Path("config.yaml")
if not p.is_file():
    print("Нет config.yaml — используйте: bash scripts/install_production_config.sh")
    raise SystemExit(1)
data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
pos = data.setdefault("positions", {})
if not isinstance(pos, dict):
    pos = {}
    data["positions"] = pos
ex = pos.get("exit_management")
if not isinstance(ex, dict):
    pos["exit_management"] = {
        "enabled": True,
        "time_stop_enabled": True,
        "time_stop_minutes": 120,
        "time_stop_min_atr_progress": 0.25,
        "close_on_time_stop": True,
        "early_breakeven_enabled": True,
        "early_breakeven_atr_mult": 0.45,
        "early_breakeven_pct": 0.18,
        "late_breakeven_enabled": True,
        "late_breakeven_retrace_pct": 40,
        "close_on_late_retrace": False,
        "late_tighten_distance_factor": 0.55,
    }
    print("OK: добавлен positions.exit_management")

fb = data.setdefault("feedback_loop", {})
if isinstance(fb, dict):
    fb.setdefault("sync_journal_before_retrain", True)
    fb.setdefault("include_soft_labels", True)
    fb.setdefault("min_feedback_label_abs_pnl_pct", 0.25)
    fb.setdefault("min_feedback_label_hold_minutes", 5)
    fb.setdefault("soft_min_feedback_label_abs_pnl_pct", 0.12)
    fb.setdefault("soft_min_feedback_label_hold_minutes", 3)
    fb.setdefault("train_split_mode", "time")
    print("OK: feedback_loop train keys")

p.write_text(yaml.safe_dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
PY

"${PY}" scripts/validate_config_yaml.py config.yaml 2>/dev/null || true
echo "Готово. Перезапуск: sudo systemctl restart trading_bot"
