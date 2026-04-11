#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from engine.entry_engine import EntryEngine
from analysis.orderflow_analyzer import OrderflowSnapshot
from analysis.transformer_model import TransformerPrediction


class _Cfg:
    def __init__(self, overrides=None):
        self._data = {
            ("entry", "min_rr_ratio"): 3.0,
            ("entry", "min_target_profit_pct"): 1.5,
            ("entry", "min_stop_distance_pct"): 1.5,
            ("entry", "min_stop_atr_mult"): 1.6,
            ("entry", "require_structural_tp"): True,
            ("entry", "sl_buffer_atr_mult"): 1.0,
            ("entry", "max_entry_extension_atr"): 0.75,
            ("entry", "entry_range_atr_mult"): 0.22,
            ("entry", "zone_proximity_pct"): 0.4,
            ("entry", "max_spread_pct"): 0.08,
            ("entry", "max_funding_rate"): 0.05,
            ("entry", "entry_threshold"): 0.62,
            ("entry", "trained_model_enabled"): False,
            ("entry", "trained_model_min_prob"): 0.52,
            ("entry", "trained_model_blend"): 0.15,
            ("entry", "trained_model_weights_path"): "transformer_weights.pt",
            ("entry", "ema_trend_filter"): True,
            ("entry", "ema_fast_period"): 20,
            ("entry", "ema_slow_period"): 50,
            ("entry", "momentum_filter"): True,
            ("entry", "momentum_lookback"): 5,
            ("entry", "volume_filter"): True,
            ("entry", "volume_lookback"): 20,
            ("entry", "require_sweep"): True,
            ("entry", "require_4h_trend"): True,
            ("entry", "min_volatility_pct"): 0.08,
            ("entry", "min_orderflow_imbalance"): 1.20,
            ("entry", "min_smc_score"): 0.76,
        }
        if overrides:
            self._data.update(overrides)

    def get(self, *keys, default=None):
        return self._data.get(keys, default)


class _Market:
    can_trade = True
    atr_pct = 0.12


class _Regime:
    class _R:
        value = "trend"

    regime = _R()


class _Struct:
    class _T:
        value = "up"

    trend = _T()
    last_sweep = None
    last_bos = None
    sweep_low = 98.0
    sweep_high = 0.0
    previous_high = 103.5
    previous_low = 96.0


class _Liq:
    target_level = 103.0
    signal = 1
    distance_to_target_pct = 1.0
    magnet_direction = "bullish"


def _klines(n=80, start=100.0):
    arr = []
    price = start
    for i in range(n):
        o = price
        c = price * 1.001
        h = max(o, c) * 1.001
        l = min(o, c) * 0.999
        arr.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000 + i})
        price = c
    return arr


def _mixed_klines(n=80, start=100.0):
    """Alternating candle directions to avoid exhaustion/contra guards."""
    arr = []
    price = start
    for i in range(n):
        o = price
        c = price * (1.001 if i % 2 == 0 else 0.999)
        h = max(o, c) * 1.001
        l = min(o, c) * 0.999
        arr.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000 + i})
        price = c
    return arr


def test_entry_engine_rejects_when_4h_trend_required_but_flat():
    engine = EntryEngine(_Cfg())
    signal = engine.generate_signal(
        "BTCUSDT",
        _klines(),
        100.0,
        _Market(),
        _Regime(),
        TransformerPrediction(prob_up=0.85, prob_down=0.1, prob_flat=0.05),
        OrderflowSnapshot(normalized_imbalance=1.4, spread_pct=0.02),
        _Liq(),
        1.0,
        zone_context=None,
        structure=_Struct(),
        funding_rate=0.0,
        htf_4h_trend=0,
    )
    assert signal.should_enter is False
    assert "require_4h_trend_neutral" in signal.metadata.get("reject_reason", "")


def test_entry_engine_rejects_when_imbalance_below_floor():
    engine = EntryEngine(
        _Cfg(
            {
                ("entry", "require_sweep"): False,
            }
        )
    )
    signal = engine.generate_signal(
        "BTCUSDT",
        _mixed_klines(),
        100.0,
        _Market(),
        _Regime(),
        TransformerPrediction(prob_up=0.85, prob_down=0.1, prob_flat=0.05),
        OrderflowSnapshot(normalized_imbalance=0.05, spread_pct=0.02),
        _Liq(),
        1.0,
        zone_context=None,
        structure=_Struct(),
        funding_rate=0.0,
        htf_4h_trend=1,
    )
    assert signal.should_enter is False
    assert "orderflow_imbalance_too_low" in signal.metadata.get("reject_reason", "")


def test_entry_engine_accepts_ratio_style_imbalance_floor_conversion():
    engine = EntryEngine(_Cfg({("entry", "min_orderflow_imbalance"): 1.20}))
    signal = engine.generate_signal(
        "BTCUSDT",
        _klines(),
        100.0,
        _Market(),
        _Regime(),
        TransformerPrediction(prob_up=0.85, prob_down=0.1, prob_flat=0.05),
        OrderflowSnapshot(normalized_imbalance=0.25, spread_pct=0.02),
        _Liq(),
        1.0,
        zone_context=None,
        structure=_Struct(),
        funding_rate=0.0,
        htf_4h_trend=1,
    )
    assert "orderflow_imbalance_too_low" not in signal.metadata.get("reject_reason", "")

