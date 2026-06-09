#!/usr/bin/env python3
"""Позиции бота сохраняют origin=bot после рестарта (registry + journal)."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.positions.position_steward import PositionSteward


def _cfg(root: Path):
    return {
        "_root": str(root),
        "positions": {"adopt_manual": True, "trailing_enabled": True},
    }


def test_registry_restores_bot_origin_after_restart(tmp_path: Path):
    cfg = _cfg(tmp_path)
    steward1 = PositionSteward(cfg)
    steward1.mark_bot_opened("ETHUSDT", take_profit=4000.0, stop_loss=3500.0)

    steward2 = PositionSteward(cfg)
    row = {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": 0.1,
        "avgPrice": 3800.0,
        "markPrice": 3850.0,
        "stopLoss": 3500.0,
        "takeProfit": 4000.0,
    }
    adopted = steward2._adopt_from_exchange(row)
    assert adopted is not None
    assert adopted.origin == "bot"


def test_apply_config_keeps_tracked_positions(tmp_path: Path):
    cfg = _cfg(tmp_path)
    steward = PositionSteward(cfg)
    row = {
        "symbol": "SOLUSDT",
        "side": "Buy",
        "size": 1.0,
        "avgPrice": 150.0,
        "markPrice": 151.0,
        "stopLoss": 145.0,
        "takeProfit": 160.0,
    }
    adopted = steward._adopt_from_exchange(row)
    assert adopted is not None
    steward._tracked["SOLUSDT"] = adopted
    cfg["positions"]["trailing_activation_pct"] = 0.55
    steward.apply_config(cfg)
    assert "SOLUSDT" in steward._tracked
    assert steward.activation_pct == 0.55


def test_journal_hydrates_open_bot_symbols(tmp_path: Path):
    journal = tmp_path / "data" / "trades" / "trade_history.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        json.dumps(
            {
                "event": "entered",
                "symbol": "BTCUSDT",
                "side": "Sell",
                "source": "ta_volatility",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = _cfg(tmp_path)
    steward = PositionSteward(cfg)
    steward.hydrate_open_symbols_from_journal(journal)
    assert "BTCUSDT" in steward._bot_symbols
