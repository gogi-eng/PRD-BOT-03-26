"""Регресс: флаги панели unified bot не затираются signal agent при _save_state."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.ops.runtime_controls import (
    load_runtime_controls,
    save_runtime_controls,
    set_runtime_trailing_override,
    effective_trailing_enabled,
)


def test_save_state_preserves_panel_flags_from_disk(tmp_path: Path) -> None:
    root = tmp_path
    state_path = root / "telegram_signal_agent_state.json"
    save_runtime_controls(
        root,
        {
            "pause_all_execution": True,
            "signal_only_mode": True,
            "channel_auto_execute": False,
            "market_scanner_auto_execute": False,
            "trailing_user_override": False,
        },
    )

    stale_state = {
        "seen": ["x"],
        "agent_runtime_controls": {
            "pause_all_execution": False,
            "signal_only_mode": False,
            "channel_auto_execute": True,
            "market_scanner_auto_execute": True,
        },
    }

    stale_state["agent_runtime_controls"] = load_runtime_controls(root)
    state_path.write_text(json.dumps(stale_state, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    rtc = loaded["agent_runtime_controls"]
    assert rtc["pause_all_execution"] is True
    assert rtc["signal_only_mode"] is True
    assert rtc["channel_auto_execute"] is False
    assert rtc["trailing_user_override"] is False


def test_trailing_override_survives_config_reload(tmp_path: Path) -> None:
    root = tmp_path
    cfg = {"positions": {"trailing_enabled": True}}
    set_runtime_trailing_override(root, False)
    assert effective_trailing_enabled(cfg, root) is False
    cfg["positions"]["trailing_enabled"] = True
    assert effective_trailing_enabled(cfg, root) is False
