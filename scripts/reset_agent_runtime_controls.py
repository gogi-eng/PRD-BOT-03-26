#!/usr/bin/env python3
"""Сброс agent_runtime_controls в telegram_signal_agent_state.json из config.yaml."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config


def main() -> int:
    cfg = load_config(ROOT / "config.yaml")
    tsa = cfg.get("telegram_signal_agent") or {}
    state_path = ROOT / str(tsa.get("state_path", "telegram_signal_agent_state.json"))
    if not state_path.exists():
        print(f"Нет файла состояния: {state_path}")
        return 1

    state = json.loads(state_path.read_text(encoding="utf-8"))
    rtc = state.setdefault("agent_runtime_controls", {})
    rtc["pause_all_execution"] = False
    rtc["channel_auto_execute"] = bool(tsa.get("auto_execute", False))
    rtc["market_scanner_auto_execute"] = bool(tsa.get("market_scanner_auto_execute", False))
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OK agent_runtime_controls:")
    print(f"  channel_auto_execute={rtc['channel_auto_execute']}")
    print(f"  market_scanner_auto_execute={rtc['market_scanner_auto_execute']}")
    print(f"  pause_all_execution={rtc['pause_all_execution']}")
    print("Перезапуск: sudo systemctl restart telegram_signal_agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
