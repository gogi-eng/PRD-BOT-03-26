"""Минимальный hold/conf перед opposite exit (кейс XAUUSDT flip)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from prd_agent.positions.opposite_signal_policy import (
    should_block_opposite_exit_for_weak_or_young,
    signal_confidence_pct,
)
from prd_agent.positions.scanner_reversal_sl import (
    handle_scanner_reversal,
    passes_reversal_filters,
)


@dataclass
class _Setup:
    symbol: str
    scenario: str
    score: int


def test_signal_confidence_pct_fraction_and_percent():
    assert signal_confidence_pct(0.72) == 72.0
    assert signal_confidence_pct(72) == 72.0


def test_block_young_position():
    blocked, why = should_block_opposite_exit_for_weak_or_young(
        confidence=0.8,
        position_age_min=15.0,
        positions_cfg={
            "opposite_signal_exit": {
                "min_position_age_min": 20,
                "min_confidence_pct": 0,
            }
        },
    )
    assert blocked
    assert "age" in why


def test_block_weak_confidence():
    blocked, why = should_block_opposite_exit_for_weak_or_young(
        confidence=0.65,
        position_age_min=30.0,
        positions_cfg={
            "opposite_signal_exit": {
                "min_position_age_min": 0,
                "min_confidence_pct": 68,
            }
        },
    )
    assert blocked
    assert "conf" in why


def test_allow_when_gates_pass():
    blocked, _ = should_block_opposite_exit_for_weak_or_young(
        confidence=0.75,
        position_age_min=25.0,
        positions_cfg={
            "opposite_signal_exit": {
                "min_position_age_min": 20,
                "min_confidence_pct": 68,
            }
        },
    )
    assert not blocked


def test_scanner_reversal_respects_min_position_age():
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    opened_ms = int((now.timestamp() - 600) * 1000)  # 10 min ago
    position = {
        "side": "Buy",
        "size": 0.01,
        "avgPrice": 100.0,
        "markPrice": 99.0,
        "createdTime": opened_ms,
    }
    setup = _Setup(symbol="XAUUSDT", scenario="DUMP", score=80)
    cfg = {
        "enabled": True,
        "close_on_reversal": True,
        "min_score": 72,
        "min_position_age_min": 20,
        "symbol_cooldown_sec": 0,
    }
    ok, why = passes_reversal_filters(setup, position, cfg, now=now)
    assert not ok
    assert "age" in why


@pytest.mark.asyncio
async def test_scanner_reversal_skips_close_when_too_young():
    client = AsyncMock()
    client.close_position = AsyncMock(return_value={"success": True})
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    opened_ms = int((now.timestamp() - 900) * 1000)  # 15 min
    setup = _Setup(symbol="XAUUSDT", scenario="DUMP", score=80)
    position = {
        "side": "Buy",
        "size": 0.01,
        "avgPrice": 100.0,
        "markPrice": 99.0,
        "createdTime": opened_ms,
    }
    cfg = {
        "enabled": True,
        "close_on_reversal": True,
        "tighten_sl": False,
        "min_score": 72,
        "min_position_age_min": 20,
        "alert_telegram": False,
        "symbol_cooldown_sec": 0,
    }
    res = await handle_scanner_reversal(
        setup=setup,
        position=position,
        client=client,
        cfg=cfg,
        cooldown_state={},
        now=now,
    )
    assert not res.closed
    client.close_position.assert_not_awaited()
