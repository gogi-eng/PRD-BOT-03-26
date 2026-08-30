#!/usr/bin/env python3
"""Тесты детектора 15m памп/дамп (spike scalp)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from telegram_agent.pump_dump_spike_scan import (
    SpikeScanConfig,
    analyze_spike_setup,
    candle_move_pct,
    effective_scanner_open_cap,
    market_structure_engine_from_cfg,
    spike_invalidation_and_target,
    spike_scan_allowed_now,
)


def _k(o: float, c: float, v: float = 100.0) -> dict:
    return {
        "open": o,
        "close": c,
        "high": max(o, c) * 1.002,
        "low": min(o, c) * 0.998,
        "volume": v,
    }


def test_candle_move_pct_pump():
    assert abs(candle_move_pct(_k(100.0, 103.5)) - 3.5) < 1e-9


def test_analyze_spike_detects_pump():
    cfg = SpikeScanConfig(enabled=True, min_move_pct=3.0, min_volume_ratio=1.0)
    base = [_k(100.0, 100.1, 80.0) for _ in range(8)]
    impulse = _k(100.0, 103.4, 220.0)
    klines = base + [impulse]
    row = analyze_spike_setup(symbol="SOLUSDT", klines=klines, turnover_24h=12_000_000, cfg=cfg)
    assert row is not None
    assert row["scenario"] == "PUMP"
    assert row["score"] >= 72
    assert row["range_pct"] >= 3.0
    assert row["atr_pct"] != row["range_pct"]
    assert row["volume_ratio"] >= 1.0


def test_analyze_spike_rejects_low_volume_zscore():
    cfg = SpikeScanConfig(
        enabled=True,
        min_move_pct=3.0,
        min_volume_ratio=2.0,
        volume_zscore_min=3.0,
    )
    base = [_k(100.0, 100.1, 100.0) for _ in range(8)]
    impulse = _k(100.0, 103.4, 110.0)
    klines = base + [impulse]
    assert analyze_spike_setup(symbol="BTCUSDT", klines=klines, turnover_24h=50_000_000, cfg=cfg) is None


def test_analyze_spike_requires_volatility_spike_when_configured():
    cfg = SpikeScanConfig(
        enabled=True,
        min_move_pct=3.0,
        min_volume_ratio=1.0,
        require_volatility_spike=True,
        atr_spike_ratio_min=50.0,
    )
    base = [_k(100.0, 100.1, 80.0) for _ in range(8)]
    impulse = _k(100.0, 103.4, 220.0)
    klines = base + [impulse]
    assert analyze_spike_setup(symbol="SOLUSDT", klines=klines, turnover_24h=12_000_000, cfg=cfg) is None


def test_analyze_spike_rejects_small_move():
    cfg = SpikeScanConfig(enabled=True, min_move_pct=3.0, min_volume_ratio=1.0)
    klines = [_k(100.0, 101.5, 200.0) for _ in range(10)]
    assert analyze_spike_setup(symbol="BTCUSDT", klines=klines, turnover_24h=50_000_000, cfg=cfg) is None


def test_spike_sl_tp_long():
    candle = _k(100.0, 103.0)
    inv, tgt = spike_invalidation_and_target(
        scenario="PUMP",
        price=103.0,
        candle=candle,
        sl_buffer_pct=0.25,
        min_rr=1.5,
    )
    assert inv < 103.0
    assert tgt > 103.0
    risk = 103.0 - inv
    assert abs((tgt - 103.0) - risk * 1.5) < 1e-6


def test_analyze_spike_rejects_without_momentum_when_required():
    cfg = SpikeScanConfig(
        enabled=True,
        min_move_pct=3.0,
        min_volume_ratio=1.0,
        require_momentum_confirmed=True,
    )
    base = [_k(100.0, 100.1, 80.0) for _ in range(8)]
    impulse = _k(100.0, 103.4, 220.0)
    klines = base + [impulse]
    assert (
        analyze_spike_setup(
            symbol="SOLUSDT",
            klines=klines,
            turnover_24h=12_000_000,
            cfg=cfg,
            momentum_confirmed=False,
        )
        is None
    )


def test_analyze_spike_accepts_with_momentum_when_required():
    cfg = SpikeScanConfig(
        enabled=True,
        min_move_pct=3.0,
        min_volume_ratio=1.0,
        require_momentum_confirmed=True,
    )
    base = [_k(100.0, 100.1, 80.0) for _ in range(8)]
    impulse = _k(100.0, 103.4, 220.0)
    klines = base + [impulse]
    row = analyze_spike_setup(
        symbol="SOLUSDT",
        klines=klines,
        turnover_24h=12_000_000,
        cfg=cfg,
        momentum_confirmed=True,
    )
    assert row is not None
    assert row["momentum_confirmed"] is True
    assert any("momentum confirmed" in reason for reason in row["reasons"])


def test_market_structure_engine_momentum_on_spike_klines():
    cfg = SpikeScanConfig(
        enabled=True,
        min_move_pct=3.0,
        min_volume_ratio=1.0,
        require_momentum_confirmed=True,
    )
    base = []
    for i in range(18):
        base.append(
            {
                "open": 100.0,
                "close": 100.05,
                "high": 100.2,
                "low": 99.9,
                "volume": 50.0,
            }
        )
    impulse = {
        "open": 100.0,
        "close": 104.0,
        "high": 104.5,
        "low": 99.8,
        "volume": 500.0,
    }
    klines = base + [impulse]
    engine = market_structure_engine_from_cfg({})
    structure = engine.analyze(klines)
    row = analyze_spike_setup(
        symbol="SOLUSDT",
        klines=klines,
        turnover_24h=12_000_000,
        cfg=cfg,
        momentum_confirmed=structure.momentum_confirmed,
    )
    if structure.momentum_confirmed:
        assert row is not None
        assert row["momentum_confirmed"] is True
    else:
        assert row is None


def test_spike_kline_limit_includes_momentum_window():
    cfg = SpikeScanConfig(
        enabled=True,
        kline_limit=12,
        require_momentum_confirmed=True,
        momentum_kline_min=20,
    )
    from telegram_agent.pump_dump_spike_scan import spike_kline_limit

    assert spike_kline_limit(cfg) == 20


def test_spike_scan_config_reads_scalp_and_position_bypass():
    cfg = {
        "market_scanner": {
            "spike_scalp": {
                "enabled": True,
                "respect_scalp_hours": False,
                "max_positions_bypass_min_score": 78,
                "extra_position_slots": 2,
            }
        },
        "trading": {"timezone_offset": 3},
    }
    sc = SpikeScanConfig.from_cfg(cfg)
    assert sc.respect_scalp_hours is False
    assert sc.max_positions_bypass_min_score == 78
    assert sc.extra_position_slots == 2


def test_spike_scan_allowed_now_respects_scalp_hours(monkeypatch):
    cfg = {
        "timezone_offset": 3,
        "market_scanner": {"spike_scalp": {"enabled": True, "respect_scalp_hours": True}},
        "trading": {
            "strategies": {"scalp_hours_local": [10, 11, 12]},
        },
    }

    monkeypatch.setattr("prd_agent.time_hours.entry_check_hour", lambda _h, _tz: 10)
    assert spike_scan_allowed_now(cfg) is True

    monkeypatch.setattr("prd_agent.time_hours.entry_check_hour", lambda _h, _tz: 3)
    assert spike_scan_allowed_now(cfg) is False


def test_spike_scan_allowed_now_when_respect_disabled():
    cfg = {
        "market_scanner": {"spike_scalp": {"enabled": True, "respect_scalp_hours": False}},
        "trading": {"timezone_offset": 3, "strategies": {"scalp_hours_local": [10]}},
    }
    assert spike_scan_allowed_now(cfg) is True


def test_effective_scanner_open_cap_spike_high_score_gets_extra_slots():
    spike_cfg = SpikeScanConfig(
        max_positions_bypass_min_score=78,
        extra_position_slots=2,
    )
    assert (
        effective_scanner_open_cap(6, source="SPIKE_SCANNER", confidence=100, spike_cfg=spike_cfg)
        == 8
    )


def test_effective_scanner_open_cap_spike_below_bypass_stays_base():
    spike_cfg = SpikeScanConfig(
        max_positions_bypass_min_score=78,
        extra_position_slots=2,
    )
    assert (
        effective_scanner_open_cap(6, source="SPIKE_SCANNER", confidence=77, spike_cfg=spike_cfg)
        == 6
    )


def test_effective_scanner_open_cap_market_scanner_never_gets_extra():
    spike_cfg = SpikeScanConfig(
        max_positions_bypass_min_score=78,
        extra_position_slots=2,
    )
    assert (
        effective_scanner_open_cap(6, source="MARKET_SCANNER", confidence=99, spike_cfg=spike_cfg)
        == 6
    )


def test_agent_world_deploy_yaml_position_limits():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / "deploy" / "config.agent_world_sandbox.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert int(cfg["trading"]["max_positions"]) == 8
    spike = cfg["market_scanner"]["spike_scalp"]
    assert int(spike["extra_position_slots"]) == 2
    assert float(spike["min_move_pct"]) == 4.0
    assert int(spike["execute_min_score"]) == 72
    assert float(spike["min_volume_ratio"]) == 1.40
    assert int(spike["max_positions_bypass_min_score"]) == 88
    assert float(cfg["trading"]["min_signal_confidence"]) == 0.85
    assert int(cfg["telegram_signal_agent"]["market_scanner_execute_min_score"]) == 75
    assert spike.get("use_dynamic_leverage") is True
    assert int(spike["max_symbols"]) == 50
    assert int(spike["top_n"]) == 1
    assert cfg["market_scanner"]["bos_scan_enabled"] is False
    # AW: own agents / каналы A+B включены (не откатывать при восстановлении SPIKE 22.08)
    assert cfg["signals"]["own_agents_enabled"] is True
    assert cfg["market_scanner"]["spike_scalp"]["auto_execute"] is True
    spike_cfg = SpikeScanConfig.from_cfg(cfg)
    cap_base = int(cfg["telegram_signal_agent"].get("auto_execute_max_open_positions") or 6)
    assert effective_scanner_open_cap(
        cap_base, source="SPIKE_SCANNER", confidence=100, spike_cfg=spike_cfg
    ) == cap_base + 2


def test_bos_scan_disabled_stops_market_scan_keeps_spike():
    from prd_agent.market.market_scanner_bridge import (
        unified_should_run_market_scan,
        unified_should_run_spike_scan,
    )

    cfg = {
        "market_scanner": {
            "enabled": True,
            "run_loop_in_unified_bot": True,
            "bos_scan_enabled": False,
            "spike_scalp": {"enabled": True, "interval_sec": 90},
        }
    }
    assert unified_should_run_market_scan(cfg) is False
    assert unified_should_run_spike_scan(cfg) is True


def test_spike_run_loop_decoupled_from_market_scanner():
    from prd_agent.market.market_scanner_bridge import (
        signal_agent_should_run_spike_scan,
        unified_should_run_spike_scan,
    )

    cfg = {
        "market_scanner": {
            "enabled": True,
            "run_loop_in_unified_bot": False,
            "run_loop_in_signal_agent": True,
            "spike_scalp": {
                "enabled": True,
                "run_loop_in_unified_bot": True,
                "run_loop_in_signal_agent": False,
            },
        }
    }
    assert unified_should_run_spike_scan(cfg) is True

    cfg_signal = {
        "market_scanner": {
            "enabled": True,
            "run_loop_in_unified_bot": True,
            "run_loop_in_signal_agent": False,
            "spike_scalp": {
                "enabled": True,
                "run_loop_in_unified_bot": False,
                "run_loop_in_signal_agent": True,
            },
        }
    }
    assert unified_should_run_spike_scan(cfg_signal) is False
    assert signal_agent_should_run_spike_scan(cfg_signal) is True
