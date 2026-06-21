"""Priority-1 derivatives guard + regime pipeline thresholds."""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from prd_agent.entry.derivatives_entry_guard import DerivativesEntryGuard
from prd_agent.entry.entry_pipeline import resolve_pipeline_threshold
from prd_agent.signals.types import UnifiedSignal
from analysis.funding_filter import FundingSignal


class _FakeExchange:
    def __init__(self, buy_ratio: float = 0.5, funding_rate: float = 0.002):
        self.buy_ratio = buy_ratio
        self.funding_rate = funding_rate

    async def get_funding_rate(self, symbol: str):
        return {"funding_rate": self.funding_rate, "open_interest": 1_000_000}

    async def get_open_interest_history(self, symbol: str, interval: str = "1h", limit: int = 25):
        return [
            {"openInterest": "1000100"},
            {"openInterest": "1000000"},
        ]

    async def get_long_short_ratio(self, symbol: str, period: str = "1h", limit: int = 10):
        return [{"buyRatio": str(self.buy_ratio), "sellRatio": str(1 - self.buy_ratio)}]


@pytest.mark.asyncio
async def test_derivatives_guard_blocks_extreme_funding_long() -> None:
    guard = DerivativesEntryGuard(
        {
            "derivatives_entry_guard": {
                "enabled": True,
                "block_on_extreme_funding": True,
            }
        }
    )
    ok, reason = await guard.check(_FakeExchange(), "BTCUSDT", "Buy")
    assert ok is False
    assert "BLOCKED" in reason or "Extreme" in reason or "funding" in reason.lower()


@pytest.mark.asyncio
async def test_derivatives_guard_blocks_crowded_lsr_long() -> None:
    guard = DerivativesEntryGuard(
        {
            "derivatives_entry_guard": {
                "enabled": True,
                "block_on_extreme_funding": False,
                "extreme_funding_threshold": 0.01,
                "long_short_ratio": {
                    "enabled": True,
                    "block_on_crowd": True,
                    "long_crowded_buy_ratio": 0.70,
                },
            }
        }
    )
    ok, reason = await guard.check(_FakeExchange(buy_ratio=0.78, funding_rate=0.0001), "ETHUSDT", "Buy")
    assert ok is False
    assert "lsr" in reason.lower()


@pytest.mark.asyncio
async def test_derivatives_guard_disabled_passes() -> None:
    guard = DerivativesEntryGuard({"derivatives_entry_guard": {"enabled": False}})
    ok, _ = await guard.check(_FakeExchange(buy_ratio=0.9), "BTCUSDT", "Buy")
    assert ok is True


def test_regime_threshold_chop_stricter() -> None:
    cfg = {
        "entry_pipeline": {
            "mode": "balanced",
            "regime_thresholds": {"enabled": True, "trend": 5.0, "chop": 6.5, "breakout": 5.5},
        }
    }
    assert resolve_pipeline_threshold(cfg, "balanced", "chop") == 6.5
    assert resolve_pipeline_threshold(cfg, "balanced", "trend") == 5.0


def test_regime_threshold_disabled_uses_mode_default() -> None:
    cfg = {"entry_pipeline": {"mode": "balanced", "regime_thresholds": {"enabled": False}}}
    assert resolve_pipeline_threshold(cfg, "balanced", "chop") == 5.0
