# -*- coding: utf-8 -*-
"""Ручная позиция: не наследовать opened_at от bot и не time-stop / Companion close."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prd_agent.positions.position_steward import PositionSteward
from prd_agent.positions.trade_companion import TradeCompanionConfig, evaluate_companion_actions


def test_adopt_manual_resets_opened_at_not_stale_bot_levels(tmp_path):
    cfg = {
        "_root": str(tmp_path),
        "positions": {
            "adopt_manual": True,
            "manual_auto_close": False,
            "trailing_enabled": True,
            "exit_management": {
                "enabled": True,
                "time_stop_enabled": True,
                "time_stop_minutes": 120,
                "close_on_time_stop": True,
            },
        },
        "trade_companion": {"enabled": False},
    }
    steward = PositionSteward(cfg)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=169)).isoformat()
    steward._bot_levels["SNDKUSDT"] = {
        "take_profit": 1300.0,
        "stop_loss": 1200.0,
        "opened_at_utc": stale,
    }
    # Символа нет в bot_symbols → origin=manual
    row = {
        "symbol": "SNDKUSDT",
        "side": "Buy",
        "size": 0.01,
        "avgPrice": 1218.0,
        "markPrice": 1218.5,
        "stopLoss": 0,
        "takeProfit": 0,
        "positionIdx": 0,
    }
    pos = steward._adopt_from_exchange(row)
    assert pos is not None
    assert pos.origin == "manual"
    age_min = (
        datetime.now(timezone.utc)
        - datetime.fromisoformat(pos.opened_at_utc.replace("Z", "+00:00"))
    ).total_seconds() / 60.0
    assert age_min < 2.0, f"opened_at must be fresh, got age={age_min:.1f}m"
    assert "SNDKUSDT" not in steward._bot_levels


def test_manual_auto_close_default_false_in_config():
    cfg = TradeCompanionConfig.from_cfg(
        {"trade_companion": {"enabled": True, "bot_positions_only": True}}
    )
    assert cfg.auto_close_manual is False


def test_giveback_still_works_for_bot_rules():
    """Регрессия: giveback для bot-правил evaluate не сломан."""
    cfg = TradeCompanionConfig(
        enabled=True,
        close_giveback_enabled=True,
        close_giveback_peak_min_pct=2.0,
        close_giveback_from_peak_pct=40.0,
        extend_tp_enabled=False,
        close_reversal_enabled=False,
        tighten_sl_on_weakness=False,
        auto_close_manual=False,
    )
    decision = evaluate_companion_actions(
        cfg=cfg,
        side="Buy",
        entry=100.0,
        price=101.0,
        take_profit=110.0,
        stop_loss=98.0,
        peak_profit_pct=5.0,
        klines=[],
        sr_params={},
    )
    assert decision is not None
    assert decision.action == "close"


import asyncio
from types import SimpleNamespace

from prd_agent.positions.trade_companion import TradeCompanionAgent
from prd_agent.positions.position_steward import TrackedPosition


class _FakeExchange:
    async def get_klines(self, *args, **kwargs):
        raise AssertionError("Companion must not fetch klines for manual")

    async def close_position(self, *args, **kwargs):
        raise AssertionError("Companion must not close manual")


def test_companion_manage_cycle_skips_manual(tmp_path):
    """Даже при bot_positions_only=false Companion не закрывает origin=manual."""
    cfg = {
        "_root": str(tmp_path),
        "trade_companion": {
            "enabled": True,
            "bot_positions_only": False,
            "auto_close_manual": False,
            "close_giveback_enabled": True,
            "close_reversal_enabled": True,
            "extend_tp_enabled": False,
            "tighten_sl_on_weakness": False,
        },
        "positions": {"adopt_manual": True, "manual_auto_close": False},
    }
    agent = TradeCompanionAgent(cfg)
    steward = PositionSteward(cfg)
    steward._tracked["TUTUSDT"] = TrackedPosition(
        symbol="TUTUSDT",
        side="Buy",
        entry=0.06,
        qty=10.0,
        stop_loss=0.05,
        take_profit=0.08,
        best_price=0.065,
        origin="manual",
        opened_at_utc=datetime.now(timezone.utc).isoformat(),
        peak_profit_pct=5.0,
    )
    positions = [
        {
            "symbol": "TUTUSDT",
            "side": "Buy",
            "size": 10.0,
            "markPrice": 0.061,
            "avgPrice": 0.06,
        }
    ]
    notes = asyncio.run(agent.manage_cycle(_FakeExchange(), positions, steward))
    assert notes == []
    assert "TUTUSDT" in steward._tracked
