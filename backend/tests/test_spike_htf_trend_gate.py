"""Тесты SPIKE HTF-фильтра: блок против 1h тренда + S/R-исключения."""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from analysis.structure_zones import StructureZone, ZoneContext
from prd_agent.entry.spike_htf_trend_gate import (
    HtfTrend,
    SpikeHtfConfig,
    decide_spike_htf_align,
    evaluate_against_trend_sr_context,
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


def _fake_support_ctx(price: float = 100.0) -> ZoneContext:
    """Зоны: поддержка рядом с ценой (для BUY против bearish)."""
    return ZoneContext(
        bullish_fvg=None,
        bearish_fvg=None,
        bullish_ob=None,
        bearish_ob=None,
        support_levels=[price * 0.998],
        resistance_levels=[price * 1.05],
        all_bullish_zones=[
            StructureZone("ob", "bullish", price * 0.997, price * 1.001, 0.9, 10, False)
        ],
        all_bearish_zones=[],
    )


def _fake_resistance_ctx(price: float = 100.0) -> ZoneContext:
    """Зоны: сопротивление рядом с ценой (для SELL против bullish)."""
    return ZoneContext(
        bullish_fvg=None,
        bearish_fvg=None,
        bullish_ob=None,
        bearish_ob=None,
        support_levels=[price * 0.95],
        resistance_levels=[price * 1.002],
        all_bullish_zones=[],
        all_bearish_zones=[
            StructureZone("ob", "bearish", price * 0.999, price * 1.003, 0.9, 10, False)
        ],
    )


def _fake_mid_ctx(price: float = 100.0) -> ZoneContext:
    """S/R далеко — нет контекста у уровня."""
    return ZoneContext(
        bullish_fvg=None,
        bearish_fvg=None,
        bullish_ob=None,
        bearish_ob=None,
        support_levels=[price * 0.90],
        resistance_levels=[price * 1.10],
        all_bullish_zones=[],
        all_bearish_zones=[],
    )


def test_read_cfg_defaults_off():
    cfg = {"market_scanner": {"spike_scalp": {}}}
    assert spike_htf_align_enabled(cfg) is False
    h = read_spike_htf_cfg(cfg)
    assert h.intervals == ("60",)
    assert h.allow_neutral is True
    assert h.sr_context_enabled is False


def test_read_cfg_enabled_sandbox_keys():
    cfg = {
        "market_scanner": {
            "spike_scalp": {
                "require_htf_trend_align": True,
                "htf_trend_intervals": ["60"],
                "htf_allow_neutral": True,
                "htf_sr_context_enabled": True,
                "htf_sr_near_pct": 0.35,
                "htf_allow_against_at_sr": True,
                "htf_allow_against_on_breakout": True,
            }
        }
    }
    assert spike_htf_align_enabled(cfg) is True
    h = read_spike_htf_cfg(cfg)
    assert h.enabled is True
    assert h.intervals == ("60",)
    assert h.sr_context_enabled is True
    assert h.sr_near_pct == 0.35
    assert h.allow_against_at_sr is True
    assert h.allow_against_on_breakout is True


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
    assert "no SR context" in d.reason


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
    assert "no SR context" in d.reason


def test_spike_scan_config_reads_htf_keys():
    cfg = {
        "market_scanner": {
            "spike_scalp": {
                "enabled": True,
                "require_htf_trend_align": True,
                "htf_trend_intervals": ["60", "240"],
                "htf_allow_neutral": False,
                "htf_kline_limit": 90,
                "htf_sr_context_enabled": True,
                "htf_sr_near_pct": 0.4,
                "htf_allow_against_at_sr": False,
                "htf_allow_against_on_breakout": True,
                "htf_sr_breakout_lookback_bars": 4,
            }
        }
    }
    sc = SpikeScanConfig.from_cfg(cfg)
    assert sc.require_htf_trend_align is True
    assert sc.htf_trend_intervals == ("60", "240")
    assert sc.htf_allow_neutral is False
    assert sc.htf_kline_limit == 90
    assert sc.htf_sr_context_enabled is True
    assert sc.htf_sr_near_pct == 0.4
    assert sc.htf_allow_against_at_sr is False
    assert sc.htf_allow_against_on_breakout is True
    assert sc.htf_sr_breakout_lookback_bars == 4


def test_sr_near_support_allows_buy_against_bearish():
    htf = SpikeHtfConfig(
        enabled=True,
        intervals=("60",),
        sr_context_enabled=True,
        sr_near_pct=0.35,
        allow_against_at_sr=True,
        allow_against_on_breakout=False,
    )
    bars = _bars_trend("down")
    price = float(bars[-1]["close"])
    with patch(
        "prd_agent.entry.spike_htf_trend_gate.detect_htf_sr_breakout",
        return_value=(False, 0.0),
    ), patch(
        "prd_agent.entry.spike_htf_trend_gate._zone_ctx_from_klines",
        return_value=_fake_support_ctx(price),
    ):
        d = evaluate_spike_htf_klines("BUY", {"60": bars}, htf)
    assert d.allowed is True
    assert "near support" in d.reason
    assert "allow" in d.reason


def test_sr_near_resistance_allows_sell_against_bullish():
    htf = SpikeHtfConfig(
        enabled=True,
        intervals=("60",),
        sr_context_enabled=True,
        sr_near_pct=0.35,
        allow_against_at_sr=True,
        allow_against_on_breakout=False,
    )
    bars = _bars_trend("up")
    price = float(bars[-1]["close"])
    with patch(
        "prd_agent.entry.spike_htf_trend_gate.detect_htf_sr_breakout",
        return_value=(False, 0.0),
    ), patch(
        "prd_agent.entry.spike_htf_trend_gate._zone_ctx_from_klines",
        return_value=_fake_resistance_ctx(price),
    ):
        d = evaluate_spike_htf_klines("SELL", {"60": bars}, htf)
    assert d.allowed is True
    assert "near resistance" in d.reason


def test_sr_breakout_allows_buy_against_bearish():
    htf = SpikeHtfConfig(
        enabled=True,
        intervals=("60",),
        sr_context_enabled=True,
        allow_against_at_sr=False,
        allow_against_on_breakout=True,
    )
    bars = _bars_trend("down")
    with patch(
        "prd_agent.entry.spike_htf_trend_gate.detect_htf_sr_breakout",
        return_value=(True, 150.0),
    ):
        d = evaluate_spike_htf_klines("BUY", {"60": bars}, htf)
    assert d.allowed is True
    assert "broke resistance" in d.reason
    assert "allow" in d.reason


def test_sr_no_context_still_blocks_against_trend():
    htf = SpikeHtfConfig(
        enabled=True,
        intervals=("60",),
        sr_context_enabled=True,
        sr_near_pct=0.35,
        allow_against_at_sr=True,
        allow_against_on_breakout=True,
    )
    bars = _bars_trend("down")
    price = float(bars[-1]["close"])
    with patch(
        "prd_agent.entry.spike_htf_trend_gate.detect_htf_sr_breakout",
        return_value=(False, 0.0),
    ), patch(
        "prd_agent.entry.spike_htf_trend_gate._zone_ctx_from_klines",
        return_value=_fake_mid_ctx(price),
    ):
        d = evaluate_spike_htf_klines("BUY", {"60": bars}, htf)
    assert d.allowed is False
    assert "no SR context" in d.reason
    assert "block" in d.reason


def test_with_trend_allows_without_sr():
    """Сигнал по тренду — S/R не нужен."""
    htf = SpikeHtfConfig(
        enabled=True,
        intervals=("60",),
        sr_context_enabled=True,
    )
    d = evaluate_spike_htf_klines("BUY", {"60": _bars_trend("up")}, htf)
    assert d.allowed is True
    assert "with bullish" in d.reason


def test_evaluate_against_sr_disabled_blocks():
    htf = SpikeHtfConfig(
        enabled=True,
        sr_context_enabled=False,
        allow_against_at_sr=True,
        allow_against_on_breakout=True,
    )
    d = evaluate_against_trend_sr_context(
        "BUY",
        trend=HtfTrend.BEARISH,
        klines=_bars_trend("down"),
        htf_cfg=htf,
        interval="60",
    )
    assert d.allowed is False
    assert "no SR context" in d.reason
