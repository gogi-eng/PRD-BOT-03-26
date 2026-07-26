"""SPIKE не закрывать по opposite own-сигналу."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.positions.bot_position_registry import register_bot_open
from prd_agent.positions.opposite_signal_policy import (
    is_own_signal_source,
    is_spike_position_source,
    lookup_open_entry_meta,
    should_skip_opposite_exit_for_spike_own,
)


def test_is_spike_position_source():
    assert is_spike_position_source("SPIKE_SCANNER")
    assert is_spike_position_source("spike_scalp")
    assert not is_spike_position_source("own_multi_agent")
    assert not is_spike_position_source("MARKET_SCANNER")


def test_is_own_signal_source():
    assert is_own_signal_source("own_multi_agent")
    assert is_own_signal_source("hybrid")
    assert not is_own_signal_source("SPIKE_SCANNER")
    assert not is_own_signal_source("telegram")


def test_skip_spike_on_own_default_on():
    assert should_skip_opposite_exit_for_spike_own(
        position_source="SPIKE_SCANNER",
        signal_source="own_multi_agent",
        positions_cfg={"opposite_signal_exit": {"enabled": True}},
    )


def test_skip_disabled_by_config():
    assert not should_skip_opposite_exit_for_spike_own(
        position_source="SPIKE_SCANNER",
        signal_source="own_multi_agent",
        positions_cfg={
            "opposite_signal_exit": {
                "enabled": True,
                "skip_spike_on_own_signal": False,
            }
        },
    )


def test_no_skip_when_position_not_spike():
    assert not should_skip_opposite_exit_for_spike_own(
        position_source="own_multi_agent",
        signal_source="own_multi_agent",
        positions_cfg={"opposite_signal_exit": {"enabled": True}},
    )


def test_no_skip_when_signal_not_own():
    assert not should_skip_opposite_exit_for_spike_own(
        position_source="SPIKE_SCANNER",
        signal_source="SPIKE_SCANNER",
        positions_cfg={"opposite_signal_exit": {"enabled": True}},
    )


def test_lookup_open_entry_meta_from_registry(tmp_path: Path):
    register_bot_open(tmp_path, "DEXEUSDT", source="SPIKE_SCANNER", pump_dump=True)
    src, pd = lookup_open_entry_meta(tmp_path, "DEXEUSDT")
    assert src == "SPIKE_SCANNER"
    assert pd is True
    assert should_skip_opposite_exit_for_spike_own(
        position_source=src,
        position_pump_dump=pd,
        signal_source="own_multi_agent",
        positions_cfg={"opposite_signal_exit": {"skip_spike_on_own_signal": True}},
    )


def test_lookup_from_trade_history(tmp_path: Path):
    trades = tmp_path / "trades"
    trades.mkdir()
    journal = trades / "trade_history.jsonl"
    journal.write_text(
        json.dumps(
            {
                "event": "entered",
                "symbol": "DEXEUSDT",
                "source": "SPIKE_SCANNER",
                "side": "Sell",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    src, _ = lookup_open_entry_meta(tmp_path, "DEXEUSDT")
    assert src == "SPIKE_SCANNER"
