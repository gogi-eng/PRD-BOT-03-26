#!/usr/bin/env python3
"""Telegram toggle: positions.adopt_manual (+ sl_tp_guard.include_manual if present)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from prd_agent.engine.orchestrator import UnifiedOrchestrator
from prd_agent.positions.position_steward import PositionSteward
from prd_agent.telegram.control_bot import ControlBot
from prd_agent.telegram.status_table import format_status_table


def _make_orch(tmp_path: Path, positions: dict) -> UnifiedOrchestrator:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"positions": positions}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)

    orch = UnifiedOrchestrator.__new__(UnifiedOrchestrator)
    orch.root = tmp_path
    orch.cfg = {"_config_path": str(config_path), "positions": dict(positions)}
    orch.position_steward = PositionSteward(orch.cfg)
    orch.improver = SimpleNamespace(sandbox_dir=sandbox)

    def _reload() -> None:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        data["_config_path"] = str(config_path)
        orch.cfg = data
        orch.position_steward.apply_config(data)

    orch.reload_config = _reload  # type: ignore[method-assign]
    return orch


def test_set_adopt_manual_off_persists_and_syncs_guard(tmp_path: Path) -> None:
    orch = _make_orch(
        tmp_path,
        {
            "adopt_manual": True,
            "manual_auto_close": False,
            "trailing_enabled": True,
            "sl_tp_guard": {"enabled": True, "include_manual": True},
        },
    )
    assert orch.position_steward.manual_auto_close is False

    msg = orch.set_adopt_manual(False)
    assert "ВЫКЛ" in msg
    assert "только свои" in msg
    assert orch.position_steward.adopt_manual is False
    assert orch.position_steward.manual_auto_close is False
    assert orch.position_steward._sl_tp_guard_cfg.include_manual is False

    saved = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert saved["positions"]["adopt_manual"] is False
    assert saved["positions"]["sl_tp_guard"]["include_manual"] is False
    assert saved["positions"].get("manual_auto_close") is False


def test_set_adopt_manual_on_without_guard_block(tmp_path: Path) -> None:
    orch = _make_orch(
        tmp_path,
        {"adopt_manual": False, "trailing_enabled": True},
    )
    msg = orch.set_adopt_manual(True)
    assert "ВКЛ" in msg
    assert orch.position_steward.adopt_manual is True
    assert orch.position_steward._sl_tp_guard_cfg.include_manual is True

    saved = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert saved["positions"]["adopt_manual"] is True
    assert "sl_tp_guard" not in saved["positions"]


def test_adopt_manual_button_label_and_status_line() -> None:
    orch = SimpleNamespace(
        position_steward=SimpleNamespace(enabled=True, adopt_manual=True),
        root=Path("."),
    )
    bot = ControlBot({"telegram": {"bot_token": "", "allowed_user_ids": []}}, orch)  # type: ignore[arg-type]
    btn = bot._adopt_manual_button()
    assert btn.text == "🖐 Ручные: ВКЛ"
    assert btn.callback_data == "act:adopt_manual_off"

    orch.position_steward.adopt_manual = False
    btn2 = bot._adopt_manual_button()
    assert btn2.text == "🖐 Ручные: ВЫКЛ"
    assert btn2.callback_data == "act:adopt_manual_on"

    html = format_status_table(
        balance=100.0,
        available=90.0,
        positions=[],
        watch_symbols=["BTCUSDT"],
        risk_snapshot={"status": "ACTIVE", "pnl_today_usdt": 0.0},
        trailing_enabled=True,
        adopt_manual=False,
    )
    assert "Ручные сделки: <code>ВЫКЛ</code>" in html
