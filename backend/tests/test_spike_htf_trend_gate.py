"""Тесты SPIKE HTF-фильтра: блок против 1h тренда."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from prd_agent.entry.spike_htf_trend_gate import (
    HtfTrend,
    SpikeHtfConfig,
    decide_spike_htf_align,
    evaluate_spike_htf_align,
    evaluate_spike_htf_klines,
    read_spike_htf_cfg,
    spike_htf_align_enabled,
    trend_from_klines,
)
from telegram_agent.pump_dump_spike_scan import SpikeScanConfig


def _bars_trend(direction: str, n: int = 80) -> list:
    """Синтетические свечи с явным трендом для EMA21/55."""
    out = []
    if direction == "up":
        base = 100.0
        for i in range(n):
            c = base + i * 0.8
            out.append(
                {
                    "open": c - 0.2,
                    "close": c,
                    "high": c + 0.3,
                    "low": c - 0.4,
                    "volume": 100.0,
                }
            )
    elif direction == "down":
        base = 200.0
        for i in range(n):
            c = base - i * 0.8
            out.append(
                {
                    "open": c + 0.2,
                    "close": c,
                    "high": c + 0.4,
                    "low": c - 0.3,
                    "volume": 100.0,
                }
            )
    else:
        for i in range(n):
            c = 100.0 + (0.05 if i % 2 == 0 else -0.05)
            out.append(
                {
                    "open": 100.0,
                    "close": c,
                    "high": 100.3,
                    "low": 99.7,
                    "volume": 100.0,
                }
            )
    return out


def test_read_cfg_defaults_off():
    cfg = {"market_scanner": {"spike_scalp": {}}}
    assert spike_htf_align_enabled(cfg) is False
    h = read_spike_htf_cfg(cfg)
    assert h.intervals == ("60",)
    assert h.allow_neutral is True


def test_read_cfg_enabled_sandbox_keys():
    cfg = {
        "market_scanner": {
            "spike_scalp": {
                "require_htf_trend_align": True,
                "htf_trend_intervals": ["60"],
                "htf_allow_neutral": True,
            }
        }
    }
    assert spike_htf_align_enabled(cfg) is True
    h = read_spike_htf_cfg(cfg)
    assert h.enabled is True
    assert h.intervals == ("60",)


def test_decide_blocks_buy_against_bearish():
    d = decide_spike_htf_align("BUY", trend=HtfTrend.BEARISH, interval="60")
    assert d.allowed is False
    assert "against" in d.reason


def test_decide_blocks_sell_against_bullish():
    d = decide_spike_htf_align("SELL", trend=HtfTrend.BULLISH, interval="60")
    assert d.allowed is False


def test_decide_allows_buy_with_bullish():
    d = decide_spike_htf_align("BUY", trend=HtfTrend.BULLISH, interval="60")
    assert d.allowed is True


def test_decide_allows_neutral_by_default():
    d = decide_spike_htf_align("BUY", trend=HtfTrend.NEUTRAL, allow_neutral=True)
    assert d.allowed is True
    d2 = decide_spike_htf_align("BUY", trend=HtfTrend.NEUTRAL, allow_neutral=False)
    assert d2.allowed is False


def test_trend_from_klines_up_and_down():
    up = trend_from_klines(_bars_trend("up"))
    down = trend_from_klines(_bars_trend("down"))
    assert up == HtfTrend.BULLISH
    assert down == HtfTrend.BEARISH


def test_evaluate_blocks_pump_against_1h_down():
    cfg = {
        "market_scanner": {
            "spike_scalp": {
                "require_htf_trend_align": True,
                "htf_trend_intervals": ["60"],
            }
        }
    }
    d = evaluate_spike_htf_align("BUY", _bars_trend("down"), cfg, interval="60")
    assert d.allowed is False
    assert d.trend_label == "bearish"


def test_evaluate_allows_when_disabled():
    cfg = {"market_scanner": {"spike_scalp": {"require_htf_trend_align": False}}}
    d = evaluate_spike_htf_align("BUY", _bars_trend("down"), cfg)
    assert d.allowed is True
    assert "disabled" in d.reason


def test_evaluate_multi_interval_any_opposite_blocks():
    htf = SpikeHtfConfig(enabled=True, intervals=("60", "240"), allow_neutral=True)
    d = evaluate_spike_htf_klines(
        "BUY",
        {"60": _bars_trend("up"), "240": _bars_trend("down")},
        htf,
    )
    assert d.allowed is False
    assert d.interval == "240"


def test_spike_scan_config_reads_htf_keys():
    cfg = {
        "market_scanner": {
            "spike_scalp": {
                "enabled": True,
                "require_htf_trend_align": True,
                "htf_trend_intervals": ["60", "240"],
                "htf_allow_neutral": False,
                "htf_kline_limit": 90,
            }
        }
    }
    sc = SpikeScanConfig.from_cfg(cfg)
    assert sc.require_htf_trend_align is True
    assert sc.htf_trend_intervals == ("60", "240")
    assert sc.htf_allow_neutral is False
    assert sc.htf_kline_limit == 90
