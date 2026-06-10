"""Фаза B: адаптивный цикл, пресеты риска, WR по источникам в отчёте."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.config_presets import apply_risk_preset
from prd_agent.engine.adaptive_loop import compute_loop_interval_sec
from prd_agent.reporting.bi_hourly import BiHourlyReporter


def test_adaptive_loop_active_when_positions_open():
    cfg = {
        "trading": {
            "loop_interval_sec": 60,
            "adaptive_loop": {
                "enabled": True,
                "base_sec": 60,
                "active_sec": 45,
                "idle_sec": 120,
            },
        }
    }
    sec = compute_loop_interval_sec(
        cfg, open_positions=2, signals_this_cycle=0, seconds_since_activity=3600
    )
    assert sec == 45


def test_adaptive_loop_idle_when_quiet():
    cfg = {
        "trading": {
            "loop_interval_sec": 60,
            "adaptive_loop": {
                "enabled": True,
                "base_sec": 60,
                "active_sec": 45,
                "idle_sec": 120,
                "idle_after_quiet_min": 30,
            },
        }
    }
    sec = compute_loop_interval_sec(
        cfg, open_positions=0, signals_this_cycle=0, seconds_since_activity=2000
    )
    assert sec == 120


def test_apply_risk_preset_writes_config(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "trading:\n  min_signal_confidence: 0.85\n"
        "quality_gate:\n  min_rr_ratio: 2.0\n"
        "risk_presets:\n  aggressive:\n    trading:\n"
        "      min_signal_confidence: 0.80\n    quality_gate:\n"
        "      min_rr_ratio: 1.8\n",
        encoding="utf-8",
    )
    cfg = {"risk_presets": {"aggressive": {"trading": {"min_signal_confidence": 0.80}}}}
    changes, _backup = apply_risk_preset(cfg_path, cfg, "aggressive")
    text = cfg_path.read_text(encoding="utf-8")
    assert "min_signal_confidence: 0.8" in text
    assert "min_rr_ratio: 1.8" in text
    assert changes


def test_bi_hourly_report_includes_source_stats(tmp_path: Path):
    journal = tmp_path / "trade_history.jsonl"
    journal.write_text(
        json.dumps(
            {
                "event": "closed",
                "ts": "2099-06-10T12:00:00+00:00",
                "symbol": "BTCUSDT",
                "source": "ta_volatility",
                "pnl": 1.5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rep = BiHourlyReporter({"telegram": {}, "reporter": {}})
    text = rep.format_report(
        positions=[],
        report_2h={"pnl_usdt": 0, "closed_trades": 0, "win_rate_pct": 0, "signals_total": 0},
        report_24h={"pnl_usdt": 0, "closed_trades": 0, "win_rate_pct": 0},
        high_conf_signals=[],
        code_changes=[],
        risk_snapshot={"status": "OK", "trades_today": 0},
        balance=1000.0,
        trade_journal_path=journal,
    )
    assert "Источники сигналов" in text
    assert "ta_volatility" in text
