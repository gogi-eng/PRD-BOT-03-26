#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from engine.entry_engine import EntryEngine


@dataclass
class MockConfig:
    data: dict = field(default_factory=dict)

    def get(self, section: str, key: str, default=None):
        return self.data.get(section, {}).get(key, default)


@dataclass
class MockMarketAnalysis:
    can_trade: bool = True
    atr_pct: float = 0.6


@dataclass
class MockRegimePrediction:
    regime: object = None

    def __post_init__(self):
        if self.regime is None:
            self.regime = type("Regime", (), {"value": "trend"})()


@dataclass
class MockTransformerPrediction:
    prob_up: float = 0.8
    prob_down: float = 0.1
    prob_flat: float = 0.1


@dataclass
class MockOrderflowSnapshot:
    spread_pct: float = 0.01
    normalized_imbalance: float = 0.2
    buy_volume: float = 1200
    sell_volume: float = 900
    bid_volume: float = 5000
    ask_volume: float = 4000


@dataclass
class MockLiqAnalysis:
    target_level: float = 0.0
    distance_to_target_pct: float = 0.0
    signal: int = 0
    magnet_direction: str = "none"


@dataclass
class MockSweep:
    direction: str = "down"


@dataclass
class MockTrend:
    value: str = "up"


@dataclass
class MockStructure:
    trend: MockTrend = field(default_factory=MockTrend)
    last_sweep: MockSweep = field(default_factory=MockSweep)
    sweep_low: float = 95.0
    sweep_high: float = 105.0
    previous_high: float = 110.0
    previous_low: float = 90.0
    last_bos: object = None


@dataclass
class MockZone:
    kind: str = "fvg"
    bias: str = "bullish"
    low: float = 99.0
    high: float = 101.0
    strength: float = 0.9
    mitigated: bool = False


@dataclass
class MockZoneContext:
    active_bull: MockZone | None = None
    active_bear: MockZone | None = None
    best_bull: MockZone | None = None
    best_bear: MockZone | None = None
    all_bearish_zones: list = field(default_factory=list)
    all_bullish_zones: list = field(default_factory=list)
    resistance_levels: list = field(default_factory=list)
    support_levels: list = field(default_factory=list)

    def price_in_bullish_zone(self, _price):
        return self.active_bull

    def price_near_bullish_zone(self, _price, _tol):
        return self.active_bull

    def price_in_bearish_zone(self, _price):
        return self.active_bear

    def price_near_bearish_zone(self, _price, _tol):
        return self.active_bear

    def best_long_entry_zone(self):
        return self.best_bull

    def best_short_entry_zone(self):
        return self.best_bear

    def structural_sl_long(self, current_price, atr):
        return current_price - atr * 2.0

    def structural_sl_short(self, current_price, atr):
        return current_price + atr * 2.0

    def structural_tp_long(self, current_price, atr):
        return current_price + atr * 2.5, current_price + atr * 5.0

    def structural_tp_short(self, current_price, atr):
        return current_price - atr * 2.5, current_price - atr * 5.0


def _make_klines(
    n: int = 60,
    start: float = 100.0,
    step: float = 0.2,
    bullish: bool = True,
    alternating: bool = False,
) -> list[dict]:
    out = []
    px = start
    for i in range(n):
        o = px
        if alternating:
            up = (i % 2 == 0) if bullish else (i % 2 != 0)
            c = px + step if up else px - step
        else:
            c = px + step if bullish else px - step
        h = max(o, c) + 0.1
        l = min(o, c) - 0.1
        out.append({"open": o, "close": c, "high": h, "low": l, "volume": 1000.0})
        px = c
    return out


def test_rejects_buy_with_negative_orderflow_imbalance():
    cfg = MockConfig(
        data={
            "entry": {
                "entry_threshold": 0.55,
                "min_orderflow_imbalance": 0.09,
                "ema_trend_filter": False,
                "momentum_filter": False,
                "volume_filter": False,
                "require_structural_tp": False,
            }
        }
    )
    engine = EntryEngine(cfg)
    signal = engine.generate_signal(
        symbol="TESTUSDT",
        klines=_make_klines(bullish=False),
        current_price=100.0,
        market_analysis=MockMarketAnalysis(),
        regime_prediction=MockRegimePrediction(),
        transformer_prediction=MockTransformerPrediction(prob_up=0.9, prob_down=0.05),
        orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=-0.35),
        liq_analysis=MockLiqAnalysis(),
        atr_value=1.0,
        zone_context=MockZoneContext(active_bull=MockZone()),
        structure=MockStructure(),
        funding_rate=0.0,
        htf_4h_trend=1,
    )
    assert signal.should_enter is False
    assert "orderflow_direction_mismatch" in signal.metadata.get("reject_reason", "")


def test_rejects_entry_too_extended_without_active_zone():
    cfg = MockConfig(
        data={
            "entry": {
                "entry_threshold": 0.55,
                "min_orderflow_imbalance": 0.09,
                "max_entry_extension_atr": 0.75,
                "ema_trend_filter": False,
                "momentum_filter": False,
                "volume_filter": False,
                "require_structural_tp": False,
            }
        }
    )
    engine = EntryEngine(cfg)
    zone = MockZone(low=98.5, high=99.0, strength=0.9)
    signal = engine.generate_signal(
        symbol="TESTUSDT",
        klines=_make_klines(alternating=True),
        current_price=101.0,
        market_analysis=MockMarketAnalysis(),
        regime_prediction=MockRegimePrediction(),
        transformer_prediction=MockTransformerPrediction(prob_up=0.9, prob_down=0.05),
        orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.4),
        liq_analysis=MockLiqAnalysis(),
        atr_value=1.0,
        zone_context=MockZoneContext(active_bull=None, best_bull=zone),
        structure=MockStructure(),
        funding_rate=0.0,
        htf_4h_trend=1,
    )
    assert signal.should_enter is False
    assert signal.metadata.get("reject_reason") == "entry_too_extended_from_zone"

